"""Publishing destinations share the existing account/route/schedule/job identity."""
from datetime import timedelta, timezone
from aiogram.exceptions import TelegramAPIError
from cryptography.fernet import InvalidToken
from sqlalchemy.orm import Session
from ..models import SocialAccount, TelegramDestinationChat, User, Event, Notification, Job, Video, Source, utcnow
from ..security import decrypt, encrypt
from ..config import get_settings
from ..integrations.social import Instagram, TikTok, request_json, ProviderError
from .catalog import safe_source_rights
from .scheduling import schedule_for_account


class DestinationError(ValueError):
    pass


def telegram_permissions(chat, member):
    status = member.status
    if status in ('left', 'kicked'):
        return False
    if chat.type == 'channel':
        return status == 'creator' or (status == 'administrator' and bool(member.can_post_messages))
    if status in ('creator', 'administrator'):
        return True
    permissions = member if status == 'restricted' else chat.permissions
    return bool(permissions and getattr(permissions, 'can_send_messages', False)
                and getattr(permissions, 'can_send_videos', False)
                and (status != 'restricted' or getattr(member, 'is_member', False)))


async def verify_telegram(bot, telegram_id, chat_id):
    chat = await bot.get_chat(chat_id)
    if chat.type not in ('channel', 'group', 'supergroup'):
        raise DestinationError('Выберите канал или группу.')
    owner = await bot.get_chat_member(chat.id, telegram_id)
    if owner.status not in ('creator', 'administrator'):
        raise DestinationError('Нужны права владельца или администратора выбранного чата.')
    me = await bot.get_me()
    member = await bot.get_chat_member(chat.id, me.id)
    if not telegram_permissions(chat, member):
        raise DestinationError('У PULSE нет прав отправлять видео. Проверьте права бота и ограничения группы.')
    return chat, member


async def connect_telegram(db, bot, user, chat_id, thread_id=None):
    chat, member = await verify_telegram(bot, user.telegram_id, chat_id)
    if thread_id and (not chat.is_forum or thread_id <= 0):
        raise DestinationError('Тема доступна только в форумной супергруппе.')
    remote_id = str(chat.id) + (f':{thread_id}' if thread_id else '')
    account = db.query(SocialAccount).filter_by(owner_id=user.id, provider='telegram', remote_id=remote_id).first()
    if not account:
        account = SocialAccount(owner_id=user.id, provider='telegram', remote_id=remote_id, encrypted_access_token='')
        db.add(account); db.flush()
    account.username = chat.username or chat.title or remote_id
    account.enabled = True
    account.publishing_enabled = False
    account.connection_status = 'READY_TO_PUBLISH'
    account.capabilities = {'video': True, 'forum': bool(chat.is_forum)}
    account.last_verified_at = utcnow()
    target = db.get(TelegramDestinationChat, account.id)
    if not target:
        target = TelegramDestinationChat(account_id=account.id, chat_id=chat.id, chat_type=chat.type)
        db.add(target)
    target.title = chat.title or ''
    target.message_thread_id = thread_id
    target.permissions = {'bot_status': member.status, 'can_send_videos': True}
    db.add(Event(owner_id=user.id, kind='destination_connected', detail=f'telegram:{account.id}'))
    db.commit()
    return account


def attention(db, account, reason):
    account.connection_status = 'NEEDS_ATTENTION'
    account.publishing_enabled = False
    db.add(Event(owner_id=account.owner_id, kind='connection_error', detail=f'account:{account.id} {reason}'))
    key = f'connection:{account.id}:{int(utcnow().timestamp() // 3600)}'
    if not db.query(Notification).filter_by(event_key=key).first():
        db.add(Notification(owner_id=account.owner_id, event_key=key, kind='connection',
                            message=f'⚠️ Направление #{account.id}: {reason}. Публикации приостановлены; проверьте подключение в PULSE.'))


async def access_token(db, account):
    """Serialize refresh across workers to avoid rotating the same refresh token twice."""
    account = db.query(SocialAccount).filter_by(id=account.id).with_for_update().one()
    expiry = account.expires_at
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    token = decrypt(account.encrypted_access_token)
    threshold = timedelta(minutes=10) if account.provider == 'tiktok' else timedelta(days=1)
    if expiry and expiry <= utcnow() + threshold:
        cfg = get_settings()
        if account.provider == 'tiktok':
            if not account.encrypted_refresh_token:
                raise DestinationError('Токен истёк; переподключите TikTok.')
            data = await request_json('POST', TikTok.root + '/oauth/token/', data={
                'client_key': cfg.tiktok_client_key, 'client_secret': cfg.tiktok_client_secret,
                'grant_type': 'refresh_token', 'refresh_token': decrypt(account.encrypted_refresh_token)})
        else:
            data = await request_json('GET', 'https://graph.instagram.com/refresh_access_token', params={
                'grant_type': 'ig_refresh_token', 'access_token': token})
        token = data['access_token']
        account.encrypted_access_token = encrypt(token)
        if data.get('refresh_token'):
            account.encrypted_refresh_token = encrypt(data['refresh_token'])
        account.expires_at = utcnow() + timedelta(seconds=int(data['expires_in']))
        db.commit()
    return token


async def check_account(db, account, bot=None):
    try:
        if not account.enabled:
            raise DestinationError('Подключение отключено.')
        if account.provider == 'telegram':
            user = db.get(User, account.owner_id)
            target = db.get(TelegramDestinationChat, account.id)
            if not target:
                raise DestinationError('Направление удалено.')
            chat, member = await verify_telegram(bot, user.telegram_id, target.chat_id)
            target.title = chat.title or ''
            target.permissions = {'bot_status': member.status, 'can_send_videos': True}
            account.username = chat.username or chat.title or str(chat.id)
            capabilities = {'video': True, 'forum': bool(chat.is_forum)}
        else:
            token = await access_token(db, account)
            scopes = set(account.scopes.replace(' ', '').split(','))
            if account.provider == 'instagram':
                profile = await request_json('GET', Instagram.graph('me'), token,
                                             params={'fields': 'id,username,account_type'})
                permissions = await request_json('GET', Instagram.graph(f'{account.remote_id}/permissions'), token)
                scopes = {p['permission'] for p in permissions.get('data', []) if p.get('status') == 'granted'}
                capabilities = {'reels': 'instagram_business_content_publish' in scopes,
                                'account_type': profile.get('account_type', 'unknown')}
                account.username = profile.get('username', account.username)
            else:
                await request_json('GET', TikTok.root + '/user/info/', token, params={'fields': 'open_id,display_name'})
                capabilities = {'draft': 'video.upload' in scopes, 'direct_scope': 'video.publish' in scopes,
                                'direct_enabled': False, 'app_audit': 'unknown'}
                if 'video.publish' in scopes:
                    capabilities['creator_info'] = await TikTok().creator_info(token)
            account.scopes = ','.join(sorted(scopes))
        account.capabilities = capabilities
        account.connection_status = 'READY_TO_PUBLISH' if any(capabilities.get(k) for k in ('video', 'reels', 'draft')) else 'LIMITED'
        account.last_verified_at = utcnow()
        db.commit()
        return account.connection_status
    except (TelegramAPIError, ProviderError, DestinationError, KeyError, ValueError, InvalidToken):
        attention(db, account, 'Права или токен недоступны; проверьте права и повторную авторизацию')
        db.commit()
        return account.connection_status


def enqueue_destinations(db: Session, owner_id: int, video_id: int, account_ids: list[int], timing='schedule'):
    if timing not in ('schedule', 'now', 'approval'):
        raise DestinationError('Неверный режим публикации.')
    video = db.query(Video).filter_by(id=video_id, owner_id=owner_id).one_or_none()
    if not video:
        raise DestinationError('Видео не найдено.')
    source = db.get(Source, video.source_id)
    accounts = db.query(SocialAccount).filter(SocialAccount.owner_id == owner_id,
                                            SocialAccount.id.in_(set(account_ids))).order_by(SocialAccount.id).with_for_update().all()
    if not accounts or len(accounts) != len(set(account_ids)):
        raise DestinationError('Направления недоступны.')
    for account in accounts:
        if not account.enabled or not account.publishing_enabled or not safe_source_rights(source, account.provider):
            raise DestinationError('Включите публикации и подтвердите права на исходное видео.')
        if not video.telegram_file_id and not video.media_path:
            raise DestinationError('Доступны только метаданные. Пришлите разрешённый исходный видеофайл в PULSE.')
        if account.provider == 'tiktok' and not (account.capabilities or {}).get('draft'):
            raise DestinationError('TikTok не предоставил video.upload. Используйте ручную передачу файла.')
    jobs = []
    for account in accounts:
        existing = db.query(Job).filter_by(video_id=video.id, account_id=account.id).first()
        if existing:
            jobs.append(existing); continue
        template = (account.settings or {}).get('caption_template', '{caption}')
        caption = template.replace('{caption}', video.original_caption or '').replace('{source}', source.title or source.username)
        job = Job(owner_id=owner_id, video_id=video.id, account_id=account.id,
                  priority=int((account.settings or {}).get('priority', 0)),
                  mode='DRAFT' if account.provider == 'tiktok' else 'APPROVAL', approved=True,
                  status='WAITING_APPROVAL' if timing == 'approval' else 'SCHEDULED',
                  scheduled_at=utcnow() if timing == 'now' else schedule_for_account(db, owner_id, account.id), caption=caption[:2200])
        if timing == 'approval': job.approved = False
        db.add(job); db.flush(); jobs.append(job)
    db.add(Event(owner_id=owner_id, kind='publication_confirmed', detail=f'video:{video_id} destinations:{account_ids}'))
    db.commit()
    return jobs
