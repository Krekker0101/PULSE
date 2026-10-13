"""Optional authorized MTProto collector. Never joins channels automatically."""
import asyncio
import logging
from datetime import timezone
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from sqlalchemy.orm import Session
from ..config import get_settings
from ..security import decrypt
from ..models import Source, User, Video, utcnow
from ..services.catalog import ingest
from ..db import SessionLocal

logger=logging.getLogger(__name__)


def configured():
    s=get_settings()
    return bool(s.telegram_api_id and s.telegram_api_hash and s.telegram_session_encrypted)


async def collect_once():
    cfg=get_settings()
    if not configured(): return {'status':'disabled','reason':'Authorized MTProto session not configured'}
    session=StringSession(decrypt(cfg.telegram_session_encrypted))
    async with TelegramClient(session,cfg.telegram_api_id,cfg.telegram_api_hash) as client:
        if not await client.is_user_authorized():
            return {'status':'unavailable','reason':'Telegram session not authorized'}
        scanned=0
        with SessionLocal() as db:
            sources=db.query(Source).filter_by(enabled=True,platform='telegram').all()
            for src in sources:
                user=db.get(User,src.owner_id)
                if not user or not user.monitoring_enabled:continue
                try:
                    entity=await client.get_entity(src.chat_id if src.chat_id else src.username)
                    src.chat_id=src.chat_id or -int('100'+str(entity.id))
                    src.access_status='mtproto_available'
                    # Read last 40 posts within configured membership permissions.
                    async for message in client.iter_messages(entity,limit=40):
                        if not message.video and not (message.document and (getattr(message.document,'mime_type','') or '').startswith('video/')):
                            continue
                        views=message.views
                        reactions=(sum(r.count for r in message.reactions.results) if message.reactions else None)
                        replies=message.replies.replies if message.replies else None
                        attr=next((a for a in getattr(message.document,'attributes',[]) if hasattr(a,'duration')),None)
                        ingest(db,src,message.id,message.date, duration=getattr(attr,'duration',None),
                               width=getattr(attr,'w',None),height=getattr(attr,'h',None),
                               caption=message.message or '', views=views,reactions=reactions,replies=replies)
                    src.last_checked_at=utcnow();db.commit();scanned+=1
                except FloodWaitError as exc:
                    logger.warning('Telegram FloodWait: %s seconds',exc.seconds)
                    await asyncio.sleep(min(exc.seconds,1800))
                except Exception:
                    src.access_status='mtproto_unavailable';db.commit()
                    logger.exception('MTProto cannot scan source %s',src.id)
        return {'status':'ok','sources':scanned}


async def run():
    cfg=get_settings()
    while True:
        try:
            if configured():await collect_once()
        except asyncio.CancelledError:raise
        except Exception:logger.exception('MTProto collection failed')
        await asyncio.sleep(cfg.source_scan_interval_seconds)


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
