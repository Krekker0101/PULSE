import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy.orm import Session
from ..config import get_settings
from ..models import OAuthFlow, SocialAccount, User, Notification, Event, utcnow
from ..security import encrypt
from ..integrations.social import request_json, ProviderError


def configuration_issues(cfg, provider: str) -> list[str]:
    if provider not in ('instagram', 'tiktok'):
        return ['Неизвестная платформа.']
    fields = ('instagram_app_id', 'instagram_app_secret') if provider == 'instagram' else ('tiktok_client_key', 'tiktok_client_secret')
    issues = [f'Не задан {field.upper()}.' for field in fields if not getattr(cfg, field, '').strip()]
    try:
        Fernet(cfg.encryption_key.encode())
    except (ValueError, TypeError):
        issues.append('ENCRYPTION_KEY отсутствует или не является действительным ключом Fernet.')
    try:
        url = urlsplit(cfg.public_base_url)
        valid = (url.scheme == 'https' and bool(url.hostname) and url.hostname not in ('localhost', '127.0.0.1', '::1')
                 and not url.username and not url.password and not url.query and not url.fragment and not url.path.rstrip('/'))
        if not valid:
            issues.append('PUBLIC_BASE_URL должен быть публичным HTTPS-адресом без пути, например https://pulse.example.com.')
    except ValueError:
        issues.append('PUBLIC_BASE_URL содержит некорректный адрес.')
    return issues


def setup_message(provider: str, issues: list[str]) -> str:
    from html import escape
    label = 'Instagram' if provider == 'instagram' else 'TikTok'
    return (f'⚙️ Подключение {label} пока не настроено:\n\n' + '\n'.join('• ' + escape(i) for i in issues) +
            '\n\nОдин раз зарегистрируйте приложение платформы и HTTPS callback. '
            'В корне проекта запустите: python scripts/pulse_local.py oauth-setup\n'
            'Секреты вводятся локально скрытым вводом, не в Telegram. Затем перезапустите PULSE. '
            'После настройки аккаунты подключаются обычной кнопкой входа.')


def begin(db: Session, owner_id: int, provider: str) -> str:
    cfg = get_settings()
    owner = db.get(User, owner_id)
    if not owner or owner.telegram_id != cfg.owner_telegram_id:
        raise HTTPException(403, 'Owner-only installation')
    if provider not in ('instagram', 'tiktok'):
        raise HTTPException(400, 'Unknown provider')
    issues = configuration_issues(cfg, provider)
    if issues:
        raise HTTPException(409, {'kind': 'oauth_configuration', 'issues': issues})
    client_id = cfg.instagram_app_id if provider == 'instagram' else cfg.tiktok_client_key
    state = secrets.token_urlsafe(40)
    flow = OAuthFlow(owner_id=owner_id, provider=provider, state_hash=hashlib.sha256(state.encode()).hexdigest(),
                     expires_at=utcnow()+timedelta(minutes=10))
    db.add(flow); db.commit()
    callback = cfg.public_base_url.rstrip('/') + f'/oauth/{provider}/callback'
    if provider == 'instagram':
        url = 'https://www.instagram.com/oauth/authorize?' + urlencode({
            'client_id': client_id, 'redirect_uri': callback, 'response_type': 'code',
            'scope': 'instagram_business_basic,instagram_business_content_publish',
            'state': state, 'enable_fb_login': '0'})
    else:
        url = 'https://www.tiktok.com/v2/auth/authorize/?' + urlencode({
            'client_key': client_id, 'redirect_uri': callback, 'response_type': 'code',
            'scope': ('user.info.basic,video.upload,video.list' if cfg.tiktok_discovery_enabled else 'user.info.basic,video.upload'), 'state': state})
    return url


async def complete(db: Session, provider: str, state: str, code: str):
    cfg = get_settings()
    if provider not in ('instagram', 'tiktok') or not state or len(state) > 200 or not code or len(code) > 4096:
        raise HTTPException(400, 'Invalid callback')
    flow = db.query(OAuthFlow).filter_by(state_hash=hashlib.sha256(state.encode()).hexdigest(), provider=provider,
                                        consumed=False).with_for_update().one_or_none()
    if flow is None: raise HTTPException(400, 'Invalid or used authorization state')
    owner = db.get(User, flow.owner_id)
    if not owner or owner.telegram_id != cfg.owner_telegram_id:
        raise HTTPException(403, 'Owner-only installation')
    exp = flow.expires_at.replace(tzinfo=__import__('datetime').timezone.utc) if flow.expires_at.tzinfo is None else flow.expires_at
    if exp < utcnow(): raise HTTPException(400, 'Authorization state expired')
    flow.consumed = True
    db.commit()  # Consume before exchanging a one-time code, so callback cannot race.
    callback = cfg.public_base_url.rstrip('/') + f'/oauth/{provider}/callback'
    try:
        if provider == 'tiktok':
            token_data = await request_json('POST', 'https://open.tiktokapis.com/v2/oauth/token/', data={
                'client_key': cfg.tiktok_client_key, 'client_secret': cfg.tiktok_client_secret,
                'code': code, 'grant_type': 'authorization_code', 'redirect_uri': callback})
            remote_id = str(token_data['open_id'])
            scope = token_data.get('scope', '')
            profile = await request_json('GET', 'https://open.tiktokapis.com/v2/user/info/', token_data['access_token'],
                                         params={'fields': 'open_id,display_name'})
            username = profile.get('data', {}).get('user', {}).get('display_name', remote_id)
        else:
            short = await request_json('POST', 'https://api.instagram.com/oauth/access_token', data={
                'client_id': cfg.instagram_app_id, 'client_secret': cfg.instagram_app_secret,
                'grant_type': 'authorization_code', 'redirect_uri': callback,
                'code': code})
            long = await request_json('GET', 'https://graph.instagram.com/access_token', params={
                'grant_type': 'ig_exchange_token', 'client_secret': cfg.instagram_app_secret,
                'access_token': short['access_token']})
            token_data = {'access_token': long['access_token'], 'expires_in': long.get('expires_in', 5184000)}
            profile = await request_json('GET', 'https://graph.instagram.com/me', token_data['access_token'],
                                         params={'fields': 'id,username,account_type'})
            remote_id = str(profile['id'])
            username = profile.get('username', remote_id)
            permissions = await request_json('GET', 'https://graph.instagram.com/' + cfg.instagram_graph_version + f'/{remote_id}/permissions', token_data['access_token'])
            scope = ','.join(p['permission'] for p in permissions.get('data', []) if p.get('status') == 'granted')
            if profile.get('account_type') not in ('BUSINESS', 'MEDIA_CREATOR', 'CREATOR'):
                raise ProviderError('Professional Instagram account required')
    except (KeyError, ProviderError) as exc:
        raise HTTPException(502, 'OAuth exchange or profile verification failed; reconnect the account') from exc
    account = db.query(SocialAccount).filter_by(owner_id=flow.owner_id, provider=provider, remote_id=remote_id).one_or_none()
    if account is None:
        account = SocialAccount(owner_id=flow.owner_id, provider=provider, remote_id=remote_id,
                                encrypted_access_token='', username=username)
        db.add(account)
    account.username = username
    account.encrypted_access_token = encrypt(token_data['access_token'])
    account.encrypted_refresh_token = encrypt(token_data['refresh_token']) if token_data.get('refresh_token') else None
    account.scopes = scope
    account.expires_at = utcnow() + timedelta(seconds=int(token_data.get('expires_in', 3600)))
    account.enabled = True
    account.publishing_enabled = False
    account.connection_status = 'CONNECTED'
    account.capabilities = ({'reels': 'instagram_business_content_publish' in scope.split(','),
                             'account_type': profile.get('account_type')} if provider == 'instagram' else
                            {'draft': 'video.upload' in scope.split(','), 'direct_scope': 'video.publish' in scope.split(','),
                             'direct_enabled': False, 'app_audit': 'unknown'})
    account.last_verified_at = utcnow()
    db.add(Event(owner_id=owner.id, kind='oauth_connected', detail=provider))
    db.add(Notification(owner_id=owner.id, event_key=f'oauth:{flow.id}', kind='oauth',
                        message=f'✅ {provider}: аккаунт подключён. Публикации выключены; откройте «Мои аккаунты» и проверьте доступ.'))
    db.commit()
    return account
