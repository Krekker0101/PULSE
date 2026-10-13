import logging
from datetime import timedelta
import httpx
from sqlalchemy import or_
from ..config import get_settings
from ..db import SessionLocal
from ..models import Notification, User, utcnow

logger=logging.getLogger(__name__)


def enqueue(db,job,state):
    key=f'job:{job.id}:{state}'
    if not db.query(Notification).filter_by(event_key=key).first():
        db.add(Notification(owner_id=job.owner_id,event_key=key,kind=state.lower(),
                            message=f'⚡ PULSE: задание #{job.id} — {state}. '+(job.error or '') +
                                    (f'\n{job.platform_url}' if job.platform_url else '')))


async def deliver_once():
    cfg=get_settings()
    if not cfg.bot_token:return
    with SessionLocal() as db:
        notification=(db.query(Notification)
                      .filter(Notification.delivered_at.is_(None),Notification.attempts<8,
                              Notification.next_attempt_at <= utcnow())
                      .order_by(Notification.id).with_for_update(skip_locked=True).first())
        if not notification:return
        user=db.get(User,notification.owner_id)
        if not user or not user.notify_enabled:
            notification.delivered_at=utcnow();db.commit();return
        notification.attempts+=1
        notification.next_attempt_at=utcnow()+timedelta(seconds=min(3600,30*2**notification.attempts))
        db.commit()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r=await client.post(f'https://api.telegram.org/bot{cfg.bot_token}/sendMessage',json={
                    'chat_id':user.telegram_id,'text':notification.message[:3000],
                    'disable_notification':False})
                r.raise_for_status()
                if not r.json().get('ok'):raise RuntimeError('Telegram sendMessage failed')
            notification.delivered_at=utcnow()
            db.commit()
        except Exception:
            logger.warning('Notification delivery failed id=%s (attempt=%s)',notification.id,notification.attempts)
