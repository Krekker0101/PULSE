"""Capability-aware source normalization; never scrape arbitrary URLs."""
from dataclasses import dataclass
from urllib.parse import urlsplit
import re

_HANDLE = re.compile(r'^[A-Za-z0-9_.-]{1,100}$')
_TG = re.compile(r'^[A-Za-z][A-Za-z0-9_]{4,31}$')
_YT_ID = re.compile(r'^UC[A-Za-z0-9_-]{22}$')
_PROVIDERS = {
    'youtube': ('youtube.com', 'www.youtube.com', 'm.youtube.com'),
    'telegram': ('t.me', 'www.t.me', 'telegram.me'),
    'instagram': ('instagram.com', 'www.instagram.com'),
    'tiktok': ('tiktok.com', 'www.tiktok.com', 'm.tiktok.com'),
    'facebook': ('facebook.com', 'www.facebook.com', 'm.facebook.com'),
    'vk': ('vk.com', 'www.vk.com'),
    'rutube': ('rutube.ru', 'www.rutube.ru'),
    'x': ('x.com', 'www.x.com', 'twitter.com'),
    'twitch': ('twitch.tv', 'www.twitch.tv'),
    'bluesky': ('bsky.app',),
    'reddit': ('reddit.com', 'www.reddit.com'),
}

@dataclass(frozen=True)
class SourceReference:
    provider: str
    key: str
    label: str
    url: str
    status: str
    note: str


def normalize_public_source(value: str) -> SourceReference:
    text = value.strip()
    if not text or len(text) > 400 or any(ord(c) < 32 for c in text):
        raise ValueError('Введите ссылку на канал или профиль (до 400 символов).')
    # Default handles refer to YouTube, the primary PULSE discovery platform.
    if text.startswith('@'):
        return _make('youtube', text[1:], 'handle')
    if text.lower().startswith('tg:@'):
        return _make('telegram', text[4:], 'username')
    # Preserve the original PULSE plain Telegram username input for existing users.
    if _TG.fullmatch(text):
        return _make('telegram',text,'username')
    parts = urlsplit(text)
    if parts.scheme != 'https' or parts.username or parts.password or parts.port or parts.query or parts.fragment:
        raise ValueError('Нужна HTTPS-ссылка на публичный канал/аккаунт без параметров.')
    host = (parts.hostname or '').lower().rstrip('.')
    provider = next((p for p, hosts in _PROVIDERS.items() if host in hosts), None)
    if not provider:
        raise ValueError('Платформа пока не распознана. Используйте ссылку YouTube, Telegram или поддерживаемой соцсети.')
    segs = [s for s in parts.path.strip('/').split('/') if s]
    if not segs or len(segs) > 3:
        raise ValueError('Нужна ссылка на канал или профиль, не на отдельное видео.')
    if provider == 'youtube':
        if segs[0].startswith('@') and (len(segs)==1 or (len(segs)==2 and segs[1] in ('videos','shorts','streams'))):
            return _make('youtube', segs[0][1:], 'handle')
        if len(segs) == 2 and segs[0] == 'channel' and _YT_ID.fullmatch(segs[1]):
            return _make('youtube', segs[1], 'id')
        raise ValueError('YouTube: укажите youtube.com/@handle или youtube.com/channel/UC... (ссылки /c/ требуют ручного разрешения).')
    if provider == 'telegram':
        if len(segs) != 1: raise ValueError('Telegram: требуется https://t.me/channelname.')
        return _make(provider, segs[0], 'username')
    if provider == 'tiktok':
        if len(segs) != 1 or not segs[0].startswith('@'):
            raise ValueError('TikTok: используйте https://www.tiktok.com/@username.')
        return _make(provider, segs[0][1:], 'username')
    if provider == 'bluesky':
        if len(segs) != 2 or segs[0] != 'profile':
            raise ValueError('Bluesky: используйте https://bsky.app/profile/handle.')
        return _make(provider, segs[1], 'username')
    if provider == 'rutube':
        if len(segs) != 2 or segs[0] != 'channel':
            raise ValueError('Rutube: используйте https://rutube.ru/channel/12345/.')
        return _make(provider, segs[1], 'username')
    if provider == 'reddit':
        if len(segs) != 2 or segs[0] not in ('user', 'r'):
            raise ValueError('Reddit: используйте https://reddit.com/user/name или /r/community.')
        return _make(provider, f'{segs[0]}/{segs[1]}', 'username')
    if provider == 'instagram' and len(segs)==2 and segs[1] in ('reels','videos'):
        return _make(provider,segs[0],'username')
    if len(segs) != 1:
        raise ValueError('Укажите ссылку на профиль/канал, не на публикацию.')
    return _make(provider, segs[0], 'username')


def _make(provider: str, handle: str, kind: str) -> SourceReference:
    if provider == 'telegram':
        if not _TG.fullmatch(handle): raise ValueError('Telegram username должен содержать 5–32 допустимых символа.')
    elif provider == 'youtube' and kind == 'id':
        if not _YT_ID.fullmatch(handle): raise ValueError('Некорректный YouTube Channel ID.')
    elif not _HANDLE.fullmatch(handle):
        raise ValueError('Некорректный идентификатор аккаунта.')
    slug = handle.lower() if kind != 'id' else handle
    if provider == 'youtube':
        url = 'https://www.youtube.com/channel/'+slug if kind == 'id' else 'https://www.youtube.com/@'+slug
        status, note = 'awaiting_api_key', 'Для статистики требуется YOUTUBE_API_KEY; YouTube Data API не предоставляет загрузку MP4.'
    elif provider == 'telegram':
        url = 'https://t.me/'+slug
        status, note = 'awaiting_bot_admin', 'Bot API читает новые посты только из разрешённых каналов.'
    else:
        prefix = {'instagram':'https://www.instagram.com/', 'tiktok':'https://www.tiktok.com/@',
                  'facebook':'https://www.facebook.com/', 'vk':'https://vk.com/',
                  'rutube':'https://rutube.ru/channel/', 'x':'https://x.com/',
                  'twitch':'https://www.twitch.tv/', 'bluesky':'https://bsky.app/profile/',
                  'reddit':'https://www.reddit.com/'}[provider]
        url = prefix+slug+('/' if provider in ('instagram','rutube') else '')
        status, note = ('requires_provider_access',
            'Ссылка сохранена. Сбор данных этой платформы требует отдельного официального API, OAuth и разрешений; скрапинг не включён.')
    key = slug if provider == 'telegram' else f'{provider}:{slug}'
    return SourceReference(provider, key, slug, url, status, note)


def capability(provider: str, bound: bool = False) -> dict:
    return {'provider': provider,
            'metadata_discovery': provider in ('youtube','telegram') or (bound and provider in ('instagram','tiktok')),
            'public_stats': provider == 'youtube',
            'media_download': provider == 'telegram',
            'publish_by_source_url': False,
            'requires_api_key': provider == 'youtube',
            'requires_provider_access': provider not in ('youtube','telegram'),
            'only_authorized_self_account': provider in ('instagram','tiktok')}
