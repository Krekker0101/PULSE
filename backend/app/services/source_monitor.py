"""Capability-aware recurring source discovery. No unofficial scraping or video copies."""
import asyncio
import logging
from datetime import timezone
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models import User, Source, Video, SocialAccount, utcnow
from ..config import get_settings
from ..services.catalog import ingest
from ..integrations.youtube import YouTubeClient, YouTubeError, message_id_from_remote_id
from ..integrations.connected_sources import tiktok_self_feed,instagram_self_feed,ConnectedSourceError
from ..security import decrypt

logger=logging.getLogger(__name__)


async def scan_youtube(db:Session, source:Source, client:YouTubeClient) -> int:
    latest=(db.query(Video.remote_video_id).filter(Video.source_id==source.id,Video.remote_video_id.is_not(None))
            .order_by(Video.published_at.desc()).limit(50).all())
    state=source.provider_state or {}
    channel, videos=await client.channel_and_videos(source.username.split(':',1)[-1],source.external_id,
                         state.get('uploads_id'),[v[0] for v in latest],
                         state.get('backfill_token'),bool(state.get('backfill_done',False)))
    source.external_id=channel['channel_id']
    source.provider_state={'uploads_id':channel['uploads_id'],
                           'backfill_token':channel.get('backfill_token'),
                           'backfill_done':channel.get('backfill_done',False)}
    if channel.get('title'):source.title=channel['title'][:250]
    source.access_status='youtube_api_active';source.scan_error=''
    count=0
    for item in videos:
        # We deliberately save only metadata and a YouTube URL (never an MP4).
        ingest(db,source,message_id_from_remote_id(item.remote_id),item.published_at,
               duration=item.duration,caption=item.caption,views=item.views,
               reactions=item.likes,replies=item.comments,remote_video_id=item.remote_id,
               source_url=item.url,thumbnail_url=item.thumbnail_url,commit=False)
        count+=1
    source.last_checked_at=utcnow();db.commit()
    return count


async def scan_one(db:Session,source:Source) -> int:
    if source.platform not in ('instagram','tiktok'):
        raise ConnectedSourceError('Внешний адаптер чтения недоступен.')
    account=db.get(SocialAccount,source.bound_account_id) if source.bound_account_id else None
    if not account or not account.enabled or account.owner_id!=source.owner_id or account.provider!=source.platform:
        raise ConnectedSourceError('Привяжите свой авторизованный '+source.platform+' аккаунт в настройках источника.')
    scopes=set(account.scopes.replace(' ','').split(','))
    if source.platform=='tiktok' and 'video.list' not in scopes:
        raise ConnectedSourceError('TikTok video.list не предоставлен. Переподключите аккаунт с разрешением.')
    token=decrypt(account.encrypted_access_token)
    records=(await tiktok_self_feed(token) if source.platform=='tiktok' else
             await instagram_self_feed(token,account.remote_id))
    for v in records:
        ingest(db,source,message_id_from_remote_id(v.id),v.published_at,
            duration=v.duration,caption=v.title,views=v.views,reactions=v.likes,replies=v.comments,
            remote_video_id=v.id,source_url=v.url,thumbnail_url=v.thumbnail_url,commit=False)
    source.access_status='oauth_feed_active';source.scan_error='';source.last_checked_at=utcnow()
    db.commit()
    return len(records)


async def scan_all_once(force: bool = False) -> dict:
    cfg=get_settings()
    with SessionLocal() as db:
        sources=(db.query(Source).filter(Source.platform.in_(['youtube','instagram','tiktok']),
                   Source.enabled.is_(True),Source.access_status!='removed').order_by(Source.id).all())
        scanned=0;errors=0
        for src in sources:
            user=db.get(User,src.owner_id)
            if not user or not user.monitoring_enabled: continue
            last=src.last_checked_at
            if not force and last and (utcnow()-(last if last.tzinfo else last.replace(tzinfo=timezone.utc))).total_seconds() < cfg.source_scan_interval_seconds:
                continue
            if src.platform=='youtube' and not cfg.youtube_api_key:
                src.access_status='awaiting_api_key';src.scan_error='Настройте YOUTUBE_API_KEY.';db.commit();continue
            if src.platform in ('instagram','tiktok') and not src.bound_account_id:
                src.access_status='requires_provider_access';src.scan_error='Привяжите разрешённый собственный аккаунт.';db.commit();continue
            try:
                scanned+=(await scan_youtube(db,src,YouTubeClient(cfg.youtube_api_key)) if src.platform=='youtube'
                          else await scan_one(db,src))
            except (YouTubeError,ConnectedSourceError) as e:
                db.rollback();src=db.get(Source,src.id)
                src.access_status='scan_error';src.scan_error=str(e)[:400];src.last_checked_at=utcnow();db.commit();errors+=1
                logger.warning('Source scan %s (%s): %s',src.id,src.platform,type(e).__name__)
            except Exception:
                db.rollback();src=db.get(Source,src.id)
                src.access_status='scan_error';src.scan_error='Неожиданная ошибка чтения YouTube API.'
                src.last_checked_at=utcnow();db.commit();errors+=1
                logger.exception('Source scanner failed for source %s',src.id)
            await asyncio.sleep(0)
    return {'videos_checked':scanned,'scan_errors':errors}


async def run():
    logging.basicConfig(level=logging.INFO)
    last_cleanup=None
    while True:
        try:
            from .youtube_retention import cleanup
            now=utcnow()
            if last_cleanup is None or (now-last_cleanup).total_seconds()>86400:
                with SessionLocal() as cleanup_db:
                    cleanup(cleanup_db)
                last_cleanup=now
            result=await scan_all_once()
            if result['videos_checked'] or result['scan_errors']:logger.info('YouTube source scan %s',result)
        except asyncio.CancelledError:raise
        except Exception:logger.exception('Source monitor iteration failed')
        await asyncio.sleep(max(60,get_settings().source_scan_interval_seconds//4))


if __name__=='__main__':
    asyncio.run(run())
