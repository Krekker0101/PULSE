from datetime import timezone, timedelta
from sqlalchemy.orm import Session
from ..models import Job, AccountSchedule, SocialAccount, utcnow
from ..domain.schedule import next_slot


def schedule_for_account(db:Session,owner_id:int,account_id:int):
    # Hold the destination row lock until the caller commits the reserved job.
    db.query(SocialAccount).filter_by(owner_id=owner_id,id=account_id).with_for_update().one()
    rules=db.query(AccountSchedule).filter_by(owner_id=owner_id,account_id=account_id,enabled=True).first()
    if not rules:return utcnow()
    start=utcnow()-timedelta(days=1)
    end=utcnow()+timedelta(days=33)
    reserved=(db.query(Job.scheduled_at).filter(Job.account_id==account_id,
             Job.scheduled_at>=start,Job.scheduled_at<=end,
             Job.status.in_(['SCHEDULED','UPLOADING','PLATFORM_PROCESSING','PUBLISHED']))
             .filter(Job.scheduled_at.is_not(None)).all())
    taken=[x for (x,) in reserved]
    return next_slot(utcnow(),rules.timezone,rules.days,rules.times,rules.daily_max,
                     rules.min_gap_minutes,taken)
