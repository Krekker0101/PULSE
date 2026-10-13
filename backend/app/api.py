import asyncio
import csv
import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from .config import get_settings
from .db import get_db
from .models import User, Source, Video, SocialAccount, Route, Job, Event, AccountSchedule, ClipAsset
from .security import current_user
from .services.catalog import normalize_source, ingest, safe_source_rights
from .integrations.sources import normalize_public_source, capability
from .services.oauth import begin, complete
from .services.signed_media import verify as verify_media, public_clip_url, verify_clip
from .domain.filters import evaluate
from .domain.schedule import validate_schedule
from .services.scheduling import schedule_for_account

app = FastAPI(title='PULSE', version='0.2.0', docs_url='/api/docs', redoc_url=None)
logger = logging.getLogger(__name__)


class SourceNew(BaseModel):
    username: str
    format_mode: Literal['SHORT','LONG','ALL'] = 'ALL'


class SourceEdit(BaseModel):
    enabled: bool | None = None
    format_mode: Literal['SHORT','LONG','ALL'] | None = None
    min_views: int | None = Field(None, ge=0)
    min_reactions: int | None = Field(None, ge=0)
    min_replies: int | None = Field(None, ge=0)
    max_age_hours: int | None = Field(None, ge=1, le=8760)
    min_duration: float | None = Field(None, ge=0)
    max_duration: float | None = Field(None, gt=0)
    filter_tree: dict | None = None


class RightsEdit(BaseModel):
    evidence: str = Field(min_length=10, max_length=3000)
    instagram: bool = False
    tiktok: bool = False
    derivative: bool = False
    attest_ownership_or_license: bool


class RouteNew(BaseModel):
    source_id: int
    account_id: int
    mode: Literal['AUTO','APPROVAL','DRAFT','MANUAL','OFF'] = 'APPROVAL'


class Approval(BaseModel):
    caption: str = Field(default='', max_length=2200)


def source_json(s: Source):
    return {'id':s.id, 'username':s.username,'title':s.title, 'enabled':s.enabled,
            'platform':s.platform,'url':s.canonical_url or ('https://t.me/'+s.username if s.platform=='telegram' else ''),
            'bound_account_id':s.bound_account_id,
            'capabilities':capability(s.platform,bool(s.bound_account_id)),'scan_error':s.scan_error,
            'access_status':s.access_status,'rights_status':s.rights_status,
            'rights_instagram':s.rights_instagram,'rights_tiktok':s.rights_tiktok,
            'format_mode':s.format_mode,'min_views':s.min_views,'min_reactions':s.min_reactions,
            'min_replies':s.min_replies,'max_age_hours':s.max_age_hours,
            'min_duration':s.min_duration,'max_duration':s.max_duration,
            'filter_tree':s.filter_tree,'last_checked_at':s.last_checked_at}


def video_json(v: Video):
    return {'id':v.id,'source_id':v.source_id,'message_id':v.message_id,'caption':v.original_caption,
            'source_url':v.source_url,'remote_video_id':v.remote_video_id,'thumbnail_url':v.thumbnail_url,
            'duration':v.duration,'width':v.width,'height':v.height,'views':v.views,'reactions':v.reactions,
            'replies':v.replies,'viral_score':v.viral_score,'eligible':v.eligible,
            'reason':v.filter_reason,'published_at':v.published_at, 'ready':bool(v.media_path)}


def job_json(j: Job):
    return {'id':j.id,'video_id':j.video_id,'account_id':j.account_id,'status':j.status,'mode':j.mode,
            'approved':j.approved,'scheduled_at':j.scheduled_at,'caption':j.caption,
            'platform_id':j.platform_id,'platform_url':j.platform_url,'error':j.error}


@app.get('/health/live')
def live():
    return {'status':'alive'}


@app.get('/health/ready')
def ready(db: Session = Depends(get_db)):
    try:
        db.execute(text('SELECT 1'))
    except Exception as exc:
        logger.error('Database health check failure %s', type(exc).__name__)
        raise HTTPException(503,'Database unavailable') from None
    return {'status':'ready','database':'ok'}


@app.get('/api/dashboard')
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    oid = user.id
    return {'monitoring_enabled': user.monitoring_enabled,'notify_enabled': user.notify_enabled,
            'sources':db.query(Source).filter_by(owner_id=oid).count(),
            'accounts':db.query(SocialAccount).filter_by(owner_id=oid,enabled=True).count(),
            'videos':db.query(Video).filter_by(owner_id=oid).count(),
            'eligible':db.query(Video).filter_by(owner_id=oid,eligible=True).count(),
            'queue':db.query(Job).filter(Job.owner_id==oid,Job.status.in_(['SCHEDULED','WAITING_APPROVAL','PLATFORM_PROCESSING'])).count(),
            'published':db.query(Job).filter_by(owner_id=oid,status='PUBLISHED').count()}


@app.post('/api/monitoring')
def toggle_monitoring(enabled: bool, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.monitoring_enabled = enabled
    db.commit()
    return {'monitoring_enabled':enabled}


@app.post('/api/notifications/settings')
def notifications_toggle(enabled:bool,user:User=Depends(current_user),db:Session=Depends(get_db)):
    user.notify_enabled=enabled;db.commit();return {'enabled':enabled}


@app.get('/api/sources')
def sources(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [source_json(s) for s in db.query(Source).filter_by(owner_id=user.id).order_by(Source.id.desc()).all()]


@app.post('/api/sources', status_code=201)
def add_source(data: SourceNew, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try: reference = normalize_public_source(data.username)
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    username = reference.key
    if db.query(Source).filter_by(owner_id=user.id,username=username).first():
        raise HTTPException(409,'Source already exists')
    source = Source(owner_id=user.id,username=username,platform=reference.provider,
                    canonical_url=reference.url,access_status=reference.status,
                    format_mode=data.format_mode)
    db.add(source); db.commit(); db.refresh(source)
    return source_json(source)


@app.get('/api/source-capabilities')
def source_capabilities(user: User = Depends(current_user)):
    return [capability(p) for p in ('youtube','telegram','instagram','tiktok','facebook','vk','rutube','x','twitch','bluesky','reddit')]


@app.post('/api/sources/{source_id}/scan')
async def scan_source(source_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    s=db.query(Source).filter_by(id=source_id,owner_id=user.id).one_or_none()
    if not s:raise HTTPException(404,'Source not found')
    if s.platform not in ('youtube','instagram','tiktok'):raise HTTPException(409,'Эта платформа требует отдельный доступ к официальному API.')
    from .services.source_monitor import scan_one, scan_youtube
    from .integrations.youtube import YouTubeClient, YouTubeError
    from .integrations.connected_sources import ConnectedSourceError
    try:
        result=(await scan_youtube(db,s,YouTubeClient(get_settings().youtube_api_key))
                if s.platform=='youtube' else await scan_one(db,s))
    except (YouTubeError, ConnectedSourceError, ValueError) as e:
        db.rollback();raise HTTPException(503,str(e)) from None
    return {'videos_checked':result,'source':source_json(s)}


class SourceConnection(BaseModel):
    account_id: int | None = None


@app.put('/api/sources/{source_id}/connection')
def bind_source_account(source_id:int,data:SourceConnection,user:User=Depends(current_user),db:Session=Depends(get_db)):
    source=db.query(Source).filter_by(id=source_id,owner_id=user.id).one_or_none()
    if not source:raise HTTPException(404,'Source not found')
    if source.platform not in ('instagram','tiktok'):
        raise HTTPException(409,'OAuth binding is only for Instagram and TikTok source accounts')
    if data.account_id is None:
        source.bound_account_id=None;source.external_id=None
        source.access_status='requires_provider_access';source.scan_error=''
    else:
        account=db.query(SocialAccount).filter_by(id=data.account_id,owner_id=user.id,enabled=True,
                                                  provider=source.platform).one_or_none()
        if account is None:raise HTTPException(404,'Matching authorized account not found')
        # Avoid silently attributing someone's video to a different Instagram profile.
        if source.platform=='instagram':
            declared=source.username.split(':',1)[-1].casefold()
            if declared != account.username.casefold():
                raise HTTPException(409,'Instagram username from URL does not match the authorized account')
        if source.platform=='tiktok' and 'video.list' not in set(account.scopes.replace(' ','').split(',')):
            raise HTTPException(409,'TikTok token lacks video.list scope. Enable TIKTOK_DISCOVERY_ENABLED and reconnect with approved scope.')
        source.bound_account_id=account.id;source.external_id=account.remote_id
        source.title=account.username[:250]
        source.access_status='oauth_account_linked';source.scan_error=''
        if source.platform=='tiktok':
            state=source.provider_state or {}
            source.provider_state={**state,'declared_profile_url':source.canonical_url}
            source.canonical_url=''  # identity not independently verified by basic OAuth
    db.commit();return source_json(source)


@app.patch('/api/sources/{source_id}')
def edit_source(source_id: int, data: SourceEdit, user: User = Depends(current_user), db: Session = Depends(get_db)):
    source = db.query(Source).filter_by(id=source_id,owner_id=user.id).one_or_none()
    if not source: raise HTTPException(404,'Source not found')
    update = data.model_dump(exclude_unset=True)
    if update.get('filter_tree') is not None:
        try: evaluate(update['filter_tree'], {})
        except ValueError as exc: raise HTTPException(422,str(exc)) from None
    for key,value in update.items(): setattr(source,key,value)
    if source.min_duration is not None and source.max_duration is not None and source.min_duration > source.max_duration:
        raise HTTPException(422,'Duration minimum exceeds maximum')
    db.commit(); return source_json(source)


@app.post('/api/sources/{source_id}/rights')
def rights(source_id: int, data: RightsEdit, user: User = Depends(current_user), db: Session = Depends(get_db)):
    source = db.query(Source).filter_by(id=source_id,owner_id=user.id).one_or_none()
    if not source: raise HTTPException(404,'Source not found')
    if not data.attest_ownership_or_license:
        raise HTTPException(409,'Ownership or applicable license must be expressly attested')
    source.rights_status='user_attested'; source.rights_evidence=data.evidence
    source.rights_instagram=data.instagram; source.rights_tiktok=data.tiktok
    source.rights_derivative=data.derivative
    db.add(Event(owner_id=user.id,kind='rights_attestation',detail=f'source={source.id}; user-attested license'))
    db.commit(); return source_json(source)


@app.delete('/api/sources/{source_id}')
def remove_source(source_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    source=db.query(Source).filter_by(id=source_id,owner_id=user.id).one_or_none()
    if not source: raise HTTPException(404,'Source not found')
    source.enabled = False; source.access_status='disabled'
    for route in db.query(Route).filter_by(source_id=source.id).all(): route.enabled=False
    for job in db.query(Job).join(Video,Job.video_id==Video.id).filter(Video.source_id==source.id,Job.status.in_(['SCHEDULED','WAITING_APPROVAL'])).all():
        job.status='CANCELLED'
    db.commit(); return {'status':'disabled','reason':'History retained for audit'}


@app.get('/api/videos')
def videos(eligible: bool | None = None, limit: int = Query(50,ge=1,le=100),
           user: User = Depends(current_user), db: Session = Depends(get_db)):
    q=db.query(Video).filter_by(owner_id=user.id)
    if eligible is not None: q=q.filter_by(eligible=eligible)
    return [video_json(x) for x in q.order_by(Video.discovered_at.desc()).limit(limit).all()]


class ClipRequest(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)


@app.post('/api/videos/{video_id}/prepare')
async def prepare_video(video_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from .integrations.telegram import download_bot_file
    from .services.media import probe
    v=db.query(Video).filter_by(id=video_id,owner_id=user.id).one_or_none()
    if not v:raise HTTPException(404,'Video not found')
    if v.media_path and Path(v.media_path).is_file():
        return {'ready':True,'info':v.media_info}
    if not v.telegram_file_id:raise HTTPException(409,'Это источник только метаданных. YouTube Data API не предоставляет MP4. Используйте исходную ссылку либо отдельно загрузите законно полученный собственный файл.')
    try:
        path,digest=await download_bot_file(v.telegram_file_id,v.id)
        info=await asyncio.to_thread(probe,Path(path))
    except Exception as exc:
        raise HTTPException(502,f'Telegram media fetch unavailable: {type(exc).__name__}') from None
    v.media_path=path;v.sha256=digest;v.media_info=info;db.commit()
    return {'ready':True,'info':info}


@app.post('/api/videos/{video_id}/suggest-clips')
async def suggest_clips(video_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from .services.clips import suggest
    v=db.query(Video).filter_by(id=video_id,owner_id=user.id).one_or_none()
    if not v:raise HTTPException(404,'Video not found')
    if not v.media_path or not Path(v.media_path).is_file():
        raise HTTPException(409,'Download/prepare source video first')
    try:return {'suggestions':await asyncio.to_thread(suggest,Path(v.media_path)),'method':'FFmpeg scene-change heuristic; verify clips manually'}
    except ValueError as exc:raise HTTPException(422,str(exc)) from None


@app.post('/api/videos/{video_id}/clips',status_code=201)
async def create_clip(video_id:int,data:ClipRequest,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from .services.media import clip,sha256_file
    from uuid import uuid4
    v=db.query(Video).filter_by(id=video_id,owner_id=user.id).one_or_none()
    if not v:raise HTTPException(404,'Video not found')
    source=db.get(Source,v.source_id)
    if not source.rights_derivative or not (source.rights_instagram or source.rights_tiktok) or source.rights_status!='user_attested':
        raise HTTPException(403,'Confirmed rights to adapt/trim the content required')
    if not v.media_path or not Path(v.media_path).is_file():
        raise HTTPException(409,'Download/prepare original video first')
    root=get_settings().storage_dir.resolve()/'clips'
    root.mkdir(parents=True,exist_ok=True)
    target=root/(uuid4().hex+'.mp4')
    try:
        info=await asyncio.to_thread(clip,Path(v.media_path),target,data.start,data.end)
        sha=await asyncio.to_thread(sha256_file,target)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    except Exception:
        logger.exception('Clip processing failed')
        raise HTTPException(500,'Clip processing failed') from None
    asset=ClipAsset(owner_id=user.id,video_id=v.id,start_seconds=data.start,end_seconds=data.end,
                    media_path=str(target),media_info=info,sha256=sha)
    db.add(asset);db.commit();db.refresh(asset)
    return {'id':asset.id,'start':asset.start_seconds,'end':asset.end_seconds,
            'info':info,'url':public_clip_url(asset.id)}


@app.get('/api/videos/{video_id}/clips')
def get_clips(video_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    v=db.query(Video).filter_by(id=video_id,owner_id=user.id).one_or_none()
    if not v:raise HTTPException(404,'Video not found')
    return [{'id':c.id,'start':c.start_seconds,'end':c.end_seconds,'url':public_clip_url(c.id)}
            for c in db.query(ClipAsset).filter_by(owner_id=user.id,video_id=video_id).all()]


@app.get('/api/accounts')
def accounts(user: User = Depends(current_user),db: Session = Depends(get_db)):
    return [{'id':a.id,'provider':a.provider,'username':a.username,'enabled':a.enabled,
             'scopes':a.scopes,'expires_at':a.expires_at} for a in db.query(SocialAccount).filter_by(owner_id=user.id).all()]


@app.delete('/api/accounts/{account_id}')
def delete_account(account_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account=db.query(SocialAccount).filter_by(id=account_id,owner_id=user.id).one_or_none()
    if not account: raise HTTPException(404,'Account not found')
    # Revoke app access from provider's own security portal if token revoke API is unavailable.
    account.enabled=False
    account.encrypted_access_token=''; account.encrypted_refresh_token=None
    for r in db.query(Route).filter_by(account_id=account.id): r.enabled=False
    for j in db.query(Job).filter(Job.account_id==account.id,Job.status.in_(['SCHEDULED','WAITING_APPROVAL'])):
        j.status='CANCELLED'
    db.commit(); return {'disabled':True,'note':'Credentials erased locally; revoke app authorization at provider as well'}


@app.post('/api/oauth/{provider}/start')
def start_oauth(provider: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {'url':begin(db,user.id,provider)}


@app.get('/oauth/{provider}/callback')
async def oauth_callback(provider: Literal['instagram','tiktok'], code: str='',state: str='',error: str='',
                         db: Session = Depends(get_db)):
    if error:
        return HTMLResponse('<h2>PULSE: авторизация отменена.</h2>',status_code=400)
    if not state or not code: raise HTTPException(400,'Missing OAuth state or code')
    account=await complete(db,provider,state,code)
    return HTMLResponse('<html><meta charset="utf-8"><body><h2>✅ PULSE</h2><p>Аккаунт подключён. Вернитесь в Telegram.</p></body></html>')


class ScheduleEdit(BaseModel):
    enabled: bool = True
    timezone: str = 'Asia/Dushanbe'
    days: list[int] = Field(default_factory=lambda:list(range(7)))
    times: list[str] = Field(default_factory=lambda:['10:00','15:00','20:00'])
    daily_max: int = Field(default=3,ge=1,le=10)
    min_gap_minutes: int = Field(default=120,ge=0,le=1440)


@app.get('/api/schedules')
def schedules(user:User=Depends(current_user),db:Session=Depends(get_db)):
    return [{'account_id':x.account_id,'enabled':x.enabled,'timezone':x.timezone,'days':x.days,
             'times':x.times,'daily_max':x.daily_max,'min_gap_minutes':x.min_gap_minutes}
            for x in db.query(AccountSchedule).filter_by(owner_id=user.id).all()]


@app.put('/api/schedules/{account_id}')
def set_schedule(account_id:int,data:ScheduleEdit,user:User=Depends(current_user),db:Session=Depends(get_db)):
    a=db.query(SocialAccount).filter_by(id=account_id,owner_id=user.id,enabled=True).first()
    if not a:raise HTTPException(404,'Social account not found')
    try:validate_schedule(data.timezone,data.days,data.times,data.daily_max,data.min_gap_minutes)
    except ValueError as e:raise HTTPException(422,str(e)) from None
    schedule=db.query(AccountSchedule).filter_by(owner_id=user.id,account_id=a.id).first()
    if not schedule:schedule=AccountSchedule(owner_id=user.id,account_id=a.id);db.add(schedule)
    for k,v in data.model_dump().items():setattr(schedule,k,v)
    db.commit();return {'account_id':account_id,'enabled':schedule.enabled,'timezone':schedule.timezone,
                        'days':schedule.days,'times':schedule.times,'daily_max':schedule.daily_max,
                        'min_gap_minutes':schedule.min_gap_minutes}


@app.get('/api/routes')
def routes(user: User = Depends(current_user),db: Session = Depends(get_db)):
    return [{'id':r.id,'source_id':r.source_id,'account_id':r.account_id,'mode':r.mode,'enabled':r.enabled}
            for r in db.query(Route).filter_by(owner_id=user.id).all()]


@app.post('/api/routes', status_code=201)
def create_route(data: RouteNew, user: User = Depends(current_user),db: Session = Depends(get_db)):
    source=db.query(Source).filter_by(id=data.source_id,owner_id=user.id).one_or_none()
    account=db.query(SocialAccount).filter_by(id=data.account_id,owner_id=user.id,enabled=True).one_or_none()
    if not source or not account: raise HTTPException(404,'Source/account not found')
    if source.platform != 'telegram' and data.mode not in ('MANUAL','OFF'):
        raise HTTPException(409,'Источник предоставляет только метаданные/ссылку; используйте MANUAL. Публикация MP4 через API недоступна.')
    if data.mode in ('AUTO','APPROVAL') and account.provider=='tiktok':
        raise HTTPException(409,'TikTok: Telegram-to-Direct-Post is not supported; choose DRAFT or MANUAL')
    if db.query(Route).filter_by(owner_id=user.id,source_id=source.id,account_id=account.id).first():
        raise HTTPException(409,'Route already exists')
    route=Route(owner_id=user.id,source_id=source.id,account_id=account.id,mode=data.mode,
                enabled=data.mode!='OFF')
    db.add(route);db.commit();db.refresh(route)
    return {'id':route.id,'mode':route.mode}


@app.delete('/api/routes/{route_id}')
def delete_route(route_id: int,user: User = Depends(current_user),db: Session = Depends(get_db)):
    route=db.query(Route).filter_by(id=route_id,owner_id=user.id).one_or_none()
    if not route: raise HTTPException(404,'Route not found')
    route.enabled=False;db.commit();return {'disabled':True}


@app.get('/api/jobs')
def jobs(user: User = Depends(current_user),db: Session = Depends(get_db)):
    return [job_json(j) for j in db.query(Job).filter_by(owner_id=user.id).order_by(Job.created_at.desc()).limit(100).all()]


@app.post('/api/jobs/{job_id}/approve')
def approve(job_id:int,data:Approval,user:User=Depends(current_user),db:Session=Depends(get_db)):
    job=db.query(Job).filter_by(id=job_id,owner_id=user.id).one_or_none()
    if not job: raise HTTPException(404,'Job not found')
    if job.status != 'WAITING_APPROVAL': raise HTTPException(409,'Approval is not available in this state')
    acc=db.get(SocialAccount,job.account_id)
    video=db.get(Video,job.video_id); source=db.get(Source,video.source_id)
    if not safe_source_rights(source,acc.provider): raise HTTPException(409,'Content usage rights not verified')
    if job.mode == 'OFF' or (acc.provider == 'tiktok' and job.mode not in ('DRAFT','MANUAL')):
        raise HTTPException(409,'Unsupported publication mode for account')
    job.approved=True; job.caption=data.caption or job.caption
    if job.mode == 'MANUAL':
        job.status='READY'; job.error='Manual publication: file/link must be published by user'
    else:
        job.status='SCHEDULED';job.scheduled_at=schedule_for_account(db,user.id,acc.id)
    db.commit();return job_json(job)


@app.get('/api/jobs/{job_id}/manual')
def manual_handoff(job_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    job=db.query(Job).filter_by(id=job_id,owner_id=user.id).one_or_none()
    if not job:raise HTTPException(404,'Job not found')
    if job.mode != 'MANUAL' or not job.approved:raise HTTPException(409,'Manual handoff not approved')
    video=db.get(Video,job.video_id);source=db.get(Source,video.source_id)
    return {'message_url':video.source_url or f'https://t.me/{source.username}/{video.message_id}',
            'caption':job.caption,'note':'Video must be downloaded/published manually; PULSE has not published it.'}


@app.post('/api/jobs/{job_id}/cancel')
def cancel(job_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    job=db.query(Job).filter_by(id=job_id,owner_id=user.id).one_or_none()
    if not job: raise HTTPException(404,'Job not found')
    if job.status not in ('WAITING_APPROVAL','SCHEDULED'):
        raise HTTPException(409,'Job cannot be safely cancelled now')
    job.status='CANCELLED';db.commit();return job_json(job)


@app.get('/api/analytics')
def analytics(user:User=Depends(current_user),db:Session=Depends(get_db)):
    return {'sources':db.query(Source).filter_by(owner_id=user.id).count(),
            'discovered':db.query(Video).filter_by(owner_id=user.id).count(),
            'eligible':db.query(Video).filter_by(owner_id=user.id,eligible=True).count(),
            'published':db.query(Job).filter_by(owner_id=user.id,status='PUBLISHED').count(),
            'failed':db.query(Job).filter_by(owner_id=user.id,status='FAILED').count()}


@app.get('/api/analytics/export.csv')
def export_csv(user:User=Depends(current_user),db:Session=Depends(get_db)):
    def safe_cell(value):
        if value is None:return ''
        string=str(value)
        if string.lstrip().startswith(('=','+','-','@')):return "'"+string
        return string
    out=io.StringIO()
    writer=csv.writer(out)
    writer.writerow(['ID','Source','Message ID','Published At UTC','Views','Reactions','Replies','Duration Seconds','Viral Score','Eligible','Filter Reason'])
    for v in db.query(Video).filter_by(owner_id=user.id).order_by(Video.id).yield_per(100):
        writer.writerow([safe_cell(x) for x in [v.id,v.source.username,v.message_id,
            v.published_at.isoformat() if v.published_at else '',v.views,v.reactions,v.replies,
            v.duration,v.viral_score,v.eligible,v.filter_reason]])
    return Response(content='\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition':'attachment; filename="pulse-analytics.csv"',
                             'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@app.get('/clips/{clip_id}')
def signed_clip(clip_id:int,expires:int,signature:str,db:Session=Depends(get_db)):
    if not verify_clip(clip_id,expires,signature):raise HTTPException(403,'Invalid or expired link')
    asset=db.get(ClipAsset,clip_id)
    if not asset:raise HTTPException(404,'Clip not found')
    source=db.get(Source,db.get(Video,asset.video_id).source_id)
    if source.rights_status!='user_attested' or not source.rights_derivative:
        raise HTTPException(403,'Content rights withdrawn')
    path=Path(asset.media_path).resolve()
    if not path.is_relative_to(get_settings().storage_dir.resolve()/'clips') or not path.is_file():
        raise HTTPException(404,'Clip unavailable')
    return FileResponse(path,media_type='video/mp4',filename='pulse-clip.mp4')


@app.get('/media/{video_id}')
def signed_media(video_id:int,expires:int,signature:str,db:Session=Depends(get_db)):
    if not verify_media(video_id,expires,signature): raise HTTPException(403,'Expired or invalid media signature')
    video=db.get(Video,video_id)
    if not video or not video.media_path: raise HTTPException(404,'Media not available')
    source=db.get(Source,video.source_id)
    if source.rights_status != 'user_attested': raise HTTPException(403,'Rights not verified')
    expected=(get_settings().storage_dir.resolve()/'originals')
    path=Path(video.media_path).resolve()
    if not path.is_relative_to(expected) or not path.is_file(): raise HTTPException(404,'Media missing')
    return FileResponse(path, media_type='video/mp4',filename='pulse-video.mp4')
