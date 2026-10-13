"""Remove expired cache files only when no unfinished publication references them."""
from datetime import timedelta
from pathlib import Path
from ..config import get_settings
from ..models import Video, Job, utcnow

TERMINAL = ('PUBLISHED', 'DRAFT_CREATED', 'FAILED', 'CANCELLED', 'SKIPPED')


def cleanup_media(db):
    cfg = get_settings()
    cutoff = utcnow() - timedelta(hours=cfg.media_retention_hours)
    protected = db.query(Job.video_id).filter(~Job.status.in_(TERMINAL))
    files = db.query(Video).filter(Video.media_path.is_not(None), ~Video.id.in_(protected)).all()
    root = (cfg.storage_dir.resolve() / 'originals')
    removed = 0
    for video in files:
        path = Path(video.media_path).resolve()
        if not path.is_relative_to(root): continue
        if path.is_file() and path.stat().st_mtime >= cutoff.timestamp(): continue
        for sibling in (path, root / f'{video.id}.mp4', root / f'{video.id}_social.mp4'):
            if sibling.is_relative_to(root): sibling.unlink(missing_ok=True)
        video.media_path = None; removed += 1
    db.commit()
    return removed
