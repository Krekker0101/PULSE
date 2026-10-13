"""Database-backed durable single-step processor. One dispatch per claim; no blind retry on publish."""
import asyncio
import logging
from datetime import timedelta, timezone
from pathlib import Path
from sqlalchemy import or_
from sqlalchemy.orm import Session
from ..config import get_settings
from ..db import SessionLocal
from ..models import Job, Video, SocialAccount, Source, utcnow
from ..domain.lifecycle import transition
from ..services.catalog import safe_source_rights
from ..services.media import probe
from ..integrations.telegram import download_bot_file
from ..integrations.social import Instagram, TikTok, ProviderError, ProviderTransportError
from .notifications import enqueue, deliver_once
from .destinations import access_token

logger = logging.getLogger(__name__)


async def worker_token(db, job, account):
    from cryptography.fernet import InvalidToken
    from .destinations import DestinationError, attention
    try:
        return await access_token(db, account)
    except (ProviderError, DestinationError, InvalidToken, KeyError, ValueError):
        attention(db, account, 'Токен недоступен; переподключите аккаунт')
        account.connection_status = 'REAUTH_REQUIRED'
        job.status = 'RECONCILIATION_REQUIRED' if job.platform_id else 'FAILED'
        job.error = 'Токен недоступен. Переподключите аккаунт; начатую публикацию проверьте на платформе.'
        enqueue(db, job, job.status); db.commit()
        return None


async def telegram_step(db, job, video, account):
    from aiogram import Bot
    from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest, TelegramForbiddenError, TelegramMigrateToChat
    from ..models import TelegramDestinationChat, PublishingAttempt
    from ..integrations.publishing import TelegramPublishingAdapter
    from .destinations import check_account, attention
    bot = Bot(get_settings().bot_token)
    try:
        if await check_account(db, account, bot) != 'READY_TO_PUBLISH':
            job.status = 'FAILED'; job.error = 'Нет прав публикации; проверьте направление'
            enqueue(db, job, 'FAILED'); db.commit(); return
        if not job.approved:
            job.status = 'WAITING_APPROVAL'; db.commit(); return
        target = db.get(TelegramDestinationChat, account.id)
        media = video.telegram_file_id
        if not media and video.media_path:
            from aiogram.types import FSInputFile
            path = Path(video.media_path).resolve()
            if path.is_relative_to(get_settings().storage_dir.resolve()) and path.is_file():
                media = FSInputFile(path)
        if not target or not media:
            job.status = 'FAILED'; job.error = 'Направление или файл Telegram недоступны'
            enqueue(db, job, 'FAILED'); db.commit(); return
        # Durable uncertainty marker prevents duplicate sends after process/network loss.
        job.status = 'UPLOADING'; job.attempts += 1
        attempt = PublishingAttempt(job_id=job.id, stage='TELEGRAM_SEND')
        db.add(attempt); db.commit()
        try:
            result = await TelegramPublishingAdapter(bot).dispatch(target=target,
                file_id=media, caption=job.caption,
                media_kind=(account.settings or {}).get('media_kind', 'video'))
            job.platform_id = result['id']; job.platform_url = result['url']
            job.status = 'PUBLISHED'; job.error = ''; attempt.outcome = 'PUBLISHED'
            enqueue(db, job, 'PUBLISHED')
        except TelegramRetryAfter as exc:
            job.status = 'SCHEDULED'; job.scheduled_at = utcnow() + timedelta(seconds=exc.retry_after)
            attempt.outcome = 'RATE_LIMITED'
        except TelegramMigrateToChat as exc:
            target.chat_id = exc.migrate_to_chat_id
            account.remote_id = str(target.chat_id) + (f':{target.message_thread_id}' if target.message_thread_id else '')
            job.status = 'SCHEDULED'; attempt.outcome = 'MIGRATED'
        except (TelegramBadRequest, TelegramForbiddenError):
            attention(db, account, 'Telegram отклонил публикацию; проверьте чат, тему и формат')
            job.status = 'FAILED'; job.error = 'Telegram отклонил публикацию'
            attempt.outcome = 'REJECTED'; enqueue(db, job, 'FAILED')
        except Exception:
            job.status = 'RECONCILIATION_REQUIRED'; job.error = 'Ответ Telegram потерян. Проверьте чат; автоматического повтора нет.'
            attempt.outcome = 'UNCERTAIN'; enqueue(db, job, 'RECONCILIATION_REQUIRED')
        db.commit()
    finally:
        await bot.session.close()


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def claim(session: Session):
    now = utcnow()
    job = (session.query(Job).filter(Job.status.in_(['SCHEDULED','DOWNLOADING','READY','PLATFORM_PROCESSING','VERIFYING']))
           .filter(Job.mode != 'MANUAL')
           .filter(or_(Job.scheduled_at.is_(None), Job.scheduled_at <= now))
           .filter(or_(Job.locked_until.is_(None), Job.locked_until < now))
           .order_by(Job.priority.desc(), Job.created_at).with_for_update(skip_locked=True).first())
    if job:
        job.locked_until = now + timedelta(minutes=75)  # exceeds the bounded FFmpeg timeout
        session.commit()
        return job.id
    session.rollback()
    return None


async def step(job_id: int):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job: return
        video = db.get(Video, job.video_id)
        source = db.get(Source, video.source_id) if video else None
        account = db.get(SocialAccount, job.account_id)
        if not video or not source or not account or account.owner_id != job.owner_id or video.owner_id != job.owner_id or not account.enabled or not safe_source_rights(source, account.provider):
            job.status = 'CANCELLED'; job.error = 'No active account or verified rights'; db.commit(); return
        if not account.publishing_enabled:
            job.scheduled_at = utcnow() + timedelta(minutes=5)
            db.commit(); return
        if account.provider == 'telegram':
            await telegram_step(db, job, video, account)
            return
        if job.status in ('SCHEDULED', 'READY'):
            from .destinations import check_account
            if await check_account(db, account) != 'READY_TO_PUBLISH':
                job.status = 'FAILED'; job.error = 'Нет разрешений публикации; проверьте подключение'
                enqueue(db, job, 'FAILED'); db.commit(); return
        if source.platform != 'telegram':
            job.status='WAITING_APPROVAL';job.error='External source is metadata-only; use MANUAL handoff, never auto-download.'
            db.commit();return
        if not job.approved:
            job.status = 'WAITING_APPROVAL'; db.commit(); return
        if account.provider == 'tiktok' and job.mode != 'DRAFT':
            job.status = 'WAITING_APPROVAL'; job.error = 'TikTok Telegram-to-Direct-Post is not permitted'; db.commit(); return
        if account.provider == 'tiktok' and not get_settings().tiktok_verified_media_domain:
            job.status = 'FAILED'; job.error = 'TikTok PULL_FROM_URL: media domain not verified'; db.commit(); return
        if job.mode not in ('AUTO', 'APPROVAL', 'DRAFT'):
            job.status = 'WAITING_APPROVAL'; job.error = 'Select a supported publication flow'; db.commit(); return
        if job.status == 'SCHEDULED':
            if video.media_path and Path(video.media_path).is_file():
                job.status = transition('SCHEDULED','READY')
            else:
                job.status = transition('SCHEDULED','DOWNLOADING')
            db.commit()
        if job.status == 'DOWNLOADING':
            if not video.telegram_file_id:
                job.status = 'FAILED'; job.error = 'Bot API file ID unavailable; add bot to channel'; db.commit(); return
            try:
                path, digest = await download_bot_file(video.telegram_file_id, video.id)
                info = await asyncio.to_thread(probe, Path(path))
                video.media_path = path; video.sha256 = digest; video.media_info = info
                job.status = transition('DOWNLOADING', 'READY')
                db.commit()
            except Exception as exc:
                job.status = 'FAILED'; job.error = f'Download/probe failed: {type(exc).__name__}'
                logger.warning('Failed to obtain video %s: %s', video.id, type(exc).__name__); db.commit(); return
        if job.status == 'READY':
            if not get_settings().public_base_url.startswith('https://'):
                job.status = 'FAILED'; job.error = 'HTTPS URL required for social API'; db.commit(); return
            from ..services.signed_media import public_url
            from ..services.media import make_variant
            info = video.media_info or {}
            if info.get('duration',0) > 900:
                job.status='FAILED';job.error='Media is longer than configured 15 minute export limit';db.commit();return
            if info.get('video_codec') not in ('h264','hevc') or 'mp4' not in str(info.get('format','')):
                if not source.rights_derivative:
                    job.status='FAILED';job.error='Video conversion requires derivative-use permission';db.commit();return
                try:
                    original=Path(video.media_path)
                    target=original.parent/f'{video.id}_social.mp4'
                    info=await asyncio.to_thread(make_variant,original,target,'fit',900)
                    video.media_path=str(target);video.media_info=info;db.commit()
                except Exception:
                    job.status='FAILED';job.error='Transcoding failed';db.commit();return
            url = public_url(video.id)
            token = await worker_token(db, job, account)
            if not token: return
            # This operation may have externally succeeded when network replies are lost.
            job.status='UPLOADING';job.error='External upload started; reconciliation required if interrupted'
            db.commit()
            try:
                if account.provider=='instagram':
                    from ..integrations.publishing import InstagramPublishingAdapter
                    container = (await InstagramPublishingAdapter().dispatch(token=token,remote_id=account.remote_id,url=url,caption=job.caption))['id']
                else:
                    from ..integrations.publishing import TikTokPublishingAdapter
                    container = (await TikTokPublishingAdapter().dispatch(token=token,url=url))['id']
            except ProviderTransportError:
                job.status='RECONCILIATION_REQUIRED';job.error='Upload initiation result uncertain'
                enqueue(db,job,job.status);db.commit();return
            except ProviderError as exc:
                job.status='FAILED'; job.error=str(exc)[:280];db.commit();return
            except Exception:
                job.status='RECONCILIATION_REQUIRED';job.error='Upload initiation result uncertain';db.commit();return
            job.platform_id=container
            job.status='PLATFORM_PROCESSING';job.error=''
            job.scheduled_at=utcnow()+timedelta(seconds=30)
            db.commit();return
        if job.status == 'VERIFYING':
            try:
                token = await worker_token(db, job, account)
                if not token: return
                info = await Instagram().media_info(token, job.platform_id)
                if str(info.get('id')) == job.platform_id:
                    job.platform_url = info.get('permalink'); job.status = 'PUBLISHED'
                    enqueue(db, job, 'PUBLISHED')
            except Exception:
                job.scheduled_at = utcnow() + timedelta(minutes=5)
            db.commit(); return
        if job.status == 'PLATFORM_PROCESSING':
            job.attempts += 1
            if job.attempts > 2880:
                job.status = 'RECONCILIATION_REQUIRED'; job.error = 'Превышен срок обработки платформой; проверьте результат'
                enqueue(db, job, job.status); db.commit(); return
            if not job.platform_id:
                job.status = 'RECONCILIATION_REQUIRED'; db.commit(); return
            token=await worker_token(db, job, account)
            if not token: return
            if account.provider=='tiktok':
                try:
                    result=await TikTok().status(token,job.platform_id)
                except Exception:
                    job.scheduled_at=utcnow()+timedelta(minutes=5);db.commit();return
                state=result.get('status')
                if state=='SEND_TO_USER_INBOX':
                    job.status='DRAFT_CREATED';job.error='Draft delivered to creator inbox; finish posting in TikTok'
                elif state=='PUBLISH_COMPLETE':
                    job.status='PUBLISHED';job.error='Published by creator through TikTok flow'
                elif state=='FAILED':
                    job.status='FAILED';job.error=f'TikTok: {result.get("fail_reason","unknown")}'
                else:
                    job.scheduled_at=utcnow()+timedelta(minutes=1)
                if job.status in ('DRAFT_CREATED','PUBLISHED','FAILED'):enqueue(db,job,job.status)
                db.commit();return
            instagram = Instagram()
            try:
                status = await instagram.container_status(token, job.platform_id)
            except Exception:
                job.scheduled_at=utcnow()+timedelta(minutes=5); db.commit(); return
            state = status.get('status_code')
            if state in ('IN_PROGRESS', 'PENDING'):
                job.scheduled_at = utcnow()+timedelta(seconds=60); db.commit(); return
            if state != 'FINISHED':
                job.status = 'FAILED'; job.error = f'Instagram container state: {state}'; db.commit(); return
            # Persist uncertainty marker BEFORE the non-idempotent publish call.
            job.status = 'RECONCILIATION_REQUIRED'
            job.error = 'Publish confirmation pending; do not retry automatically'
            db.commit()
            try:
                result = await instagram.publish_container(token, account.remote_id, job.platform_id)
                if not result.get('id'): raise ProviderError('No published media id')
                job.platform_id = str(result['id'])
                job.status = 'VERIFYING'
                job.error = ''
                db.commit()
                try:
                    info = await instagram.media_info(token, job.platform_id)
                    if str(info.get('id')) != job.platform_id: raise ProviderError('Published media verification failed')
                    job.platform_url = info.get('permalink')
                    job.status = 'PUBLISHED'; enqueue(db,job,'PUBLISHED')
                    db.commit()
                except Exception:
                    logger.warning('Published media permalink lookup failed for job %s',job.id)
                    job.scheduled_at = utcnow() + timedelta(minutes=5)
            except Exception:
                logger.warning('Publish ambiguous job %s; requires manual reconciliation', job.id)
                enqueue(db,job,job.status)
            db.commit()


async def run():
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logger.info('PULSE worker started')
    while True:
        try:
            with SessionLocal() as db:
                from .media_retention import cleanup_media
                cleanup_media(db)
                # In-flight outbound calls cannot be safely replayed after the lease expires.
                for stale in db.query(Job).filter(Job.status=='UPLOADING',or_(Job.locked_until.is_(None),Job.locked_until < utcnow())).all():
                    stale.status='RECONCILIATION_REQUIRED'
                    stale.error='Worker interrupted during external upload; verify provider before retry'
                    enqueue(db,stale,stale.status)
                db.commit()
                job_id = claim(db)
            if job_id:
                try:
                    await step(job_id)
                finally:
                    with SessionLocal() as db:
                        job = db.get(Job, job_id)
                        if job: job.locked_until = None; db.commit()
            else:
                await asyncio.sleep(settings.worker_poll_seconds)
            await deliver_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error('Worker loop failed: %s', type(exc).__name__)
            await asyncio.sleep(settings.worker_poll_seconds)


if __name__ == '__main__':
    asyncio.run(run())
