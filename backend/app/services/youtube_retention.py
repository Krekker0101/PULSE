"""Conservative YouTube API metadata retention for unauthenticated API data.

User-entered source URLs and identifiers are retained. API-sourced titles,
metrics and thumbnails are erased when not refreshed within 29 days.
Historical metrics samples older than 29 days are removed as well.
"""
from datetime import timedelta, timezone
from sqlalchemy import or_
from sqlalchemy.orm import Session
from ..models import Source,Video,Metric,utcnow


def cleanup(db:Session,now=None)->dict:
    now=now or utcnow()
    cutoff=now-timedelta(days=29)
    stale=(db.query(Video).join(Source,Source.id==Video.source_id)
           .filter(Source.platform=='youtube')
           .filter(or_(Video.metadata_checked_at < cutoff,
                       (Video.metadata_checked_at.is_(None) & (Video.discovered_at < cutoff)))).all())
    for video in stale:
        video.original_caption=''
        video.thumbnail_url=''
        video.views=None
        video.reactions=None
        video.replies=None
        video.duration=None
        video.viral_score=None
        video.eligible=False
        video.filter_reason='YouTube metadata expired; awaiting API refresh'
    old_metric_ids=db.query(Video.id).join(Source,Source.id==Video.source_id).filter(Source.platform=='youtube')
    deleted=(db.query(Metric).filter(Metric.at < cutoff,Metric.video_id.in_(old_metric_ids))
             .delete(synchronize_session=False))
    db.commit()
    return {'expired_videos':len(stale),'expired_metric_snapshots':deleted}
