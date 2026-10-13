import re
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from ..domain.filters import qualify, Result
from ..domain.popularity import viral_score
from ..models import Source, Video, Metric, Route, Job, SocialAccount, Event, Notification, utcnow
from .scheduling import schedule_for_account

USER_PATTERN = re.compile(r'^(?:https://t\.me/|@)?([A-Za-z][A-Za-z0-9_]{4,31})/?$')


def normalize_source(value: str) -> str:
    match = USER_PATTERN.fullmatch(value.strip())
    if not match:
        raise ValueError('Нужен публичный @username или https://t.me/username (5–32 символа).')
    return match.group(1).lower()


def ingest(db: Session, source: Source, message_id: int, date: datetime,
           file_id=None, duration=None, width=None, height=None, caption='',
           views=None, reactions=None, replies=None, remote_video_id=None,
           source_url='', thumbnail_url='', commit=True) -> tuple[Video, bool]:
    """Idempotent upsert, snapshots on real metric changes, routes independent per target."""
    video = (db.query(Video).filter_by(source_id=source.id, remote_video_id=remote_video_id).one_or_none()
             if remote_video_id else db.query(Video).filter_by(source_id=source.id, message_id=message_id).one_or_none())
    created = video is None
    was_eligible = bool(video.eligible) if video else False
    if created:
        video = Video(owner_id=source.owner_id, source_id=source.id, message_id=message_id,
                      published_at=date, telegram_file_id=file_id, duration=duration,
                      width=width, height=height, original_caption=caption[:4000],
                      remote_video_id=remote_video_id,source_url=source_url,thumbnail_url=thumbnail_url)
        db.add(video); db.flush()
    elif file_id and not video.telegram_file_id:
        video.telegram_file_id = file_id
    if source.platform=='youtube':
        video.metadata_checked_at=utcnow()
        if not created:
            video.duration=duration
            video.original_caption=caption[:4000]
    if source_url: video.source_url=source_url
    if thumbnail_url: video.thumbnail_url=thumbnail_url
    last = db.query(Metric).filter_by(video_id=video.id).order_by(Metric.at.desc()).first()
    supplied = {'views': views, 'reactions': reactions, 'replies': replies}
    changed = any((source.platform=='youtube' or v is not None) and
                  (last is None and v is not None or last is not None and getattr(last, k) != v)
                  for k,v in supplied.items())
    for field, value in supplied.items():
        if source.platform=='youtube' or value is not None: setattr(video, field, value)
    if changed:
        db.add(Metric(video_id=video.id, views=video.views, reactions=video.reactions, replies=video.replies))
    if source.platform=='youtube':
        # Third-party YouTube API statistics are not inputs to derived Viral Score.
        video.viral_score=None
    else:
        history = [v for (v,) in db.query(Video.views).filter_by(source_id=source.id).all()
                   if v is not None and v > 0 and v != video.views]
        growth = None
        if last and last.views is not None and video.views is not None:
            last_time = last.at if last.at.tzinfo else last.at.replace(tzinfo=timezone.utc)
            hours = (datetime.now(timezone.utc) - last_time).total_seconds()/3600
            if hours > 0.02: growth = max(0, video.views-last.views)/hours
        video.viral_score, _ = viral_score(video.views, video.reactions, video.replies, history, growth)
    outcome = qualify(source, video)
    video.eligible = outcome.result is Result.TRUE
    video.filter_reason = outcome.reason[:300]
    if video.eligible and not was_eligible:
        db.add(Notification(owner_id=video.owner_id,event_key=f'eligible:{video.id}',kind='eligible',
                            message=f'🔥 PULSE: видео #{video.id} прошло фильтры. Откройте бота → Видео → #{video.id}.'))
    if video.eligible and source.rights_status == 'user_attested':
        routes = db.query(Route).filter_by(source_id=source.id, enabled=True).all()
        for route in routes:
            account = db.get(SocialAccount, route.account_id)
            if not account or not account.enabled: continue
            allowed = safe_source_rights(source, account.provider)
            if route.mode == 'AUTO' and not account.publishing_enabled: continue
            if not allowed: continue
            if source.platform != 'telegram' and route.mode != 'MANUAL':
                continue  # YouTube and other URL-only sources must not enter auto media upload.
            if db.query(Job).filter_by(video_id=video.id, account_id=account.id).first(): continue
            status = ('SCHEDULED' if route.mode == 'AUTO' and account.provider in ('instagram', 'telegram') else 'WAITING_APPROVAL')
            # For safety TikTok direct publish is never auto-routed from Telegram.
            if account.provider == 'tiktok' and route.mode == 'AUTO':
                status = 'WAITING_APPROVAL'
            db.add(Job(owner_id=video.owner_id, video_id=video.id, account_id=account.id,
                       priority=int((account.settings or {}).get('priority',0)), mode=route.mode, status=status, approved=route.mode == 'AUTO' and account.provider in ('instagram', 'telegram'),
                       scheduled_at=schedule_for_account(db, video.owner_id, account.id) if status=='SCHEDULED' else None,
                       caption=caption[:2200]))
    if commit:
        db.commit(); db.refresh(video)
    else:
        db.flush()
    return video, created


def safe_source_rights(source: Source, platform: str, derivative: bool = False) -> bool:
    return (source.rights_status == 'user_attested' and bool(source.rights_evidence.strip())
            and (not derivative or source.rights_derivative)
            and (platform == 'telegram' or (source.rights_instagram if platform == 'instagram' else source.rights_tiktok)))
