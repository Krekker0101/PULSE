"""Official YouTube Data API v3 discovery. Metadata-only: no media downloading."""
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, parse_qs
import httpx

API = 'https://www.googleapis.com/youtube/v3'
_DURATION = re.compile(r'^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$')


class YouTubeError(RuntimeError):
    pass


_VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')


def parse_youtube_video_url(text: str) -> str | None:
    """Recognize canonical YouTube video/Shorts/live and youtu.be links, no network fetch."""
    if len(text) > 512 or any(ord(x) < 32 for x in text):
        return None
    try:
        parts = urlsplit(text.strip())
        if parts.scheme != 'https' or parts.username or parts.password or parts.port or parts.fragment:
            return None
        host = (parts.hostname or '').lower().rstrip('.')
        path = [part for part in parts.path.strip('/').split('/') if part]
        result = None
        if host in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
            if len(path) == 1 and path[0] == 'watch':
                result = (parse_qs(parts.query).get('v') or [None])[0]
            elif len(path) == 2 and path[0] in ('shorts', 'live', 'embed') and not parts.query:
                result = path[1]
        elif host in ('youtu.be', 'www.youtu.be') and len(path) == 1 and not parts.query:
            result = path[0]
        return result if isinstance(result, str) and _VIDEO_ID.fullmatch(result) else None
    except ValueError:
        return None


def duration_seconds(value: str | None) -> float | None:
    if not value: return None
    match = _DURATION.fullmatch(value)
    if not match: return None
    d,h,m,s = match.groups()
    return int(d or 0)*86400 + int(h or 0)*3600 + int(m or 0)*60 + float(s or 0)


def safe_metric(obj: dict, key: str) -> int | None:
    try:
        result=obj.get(key)
        if result is None: return None
        return max(0, int(result))
    except (TypeError, ValueError):
        return None


def message_id_from_remote_id(remote_id: str) -> int:
    """Legacy BigInt PK compatibility; full YouTube ID also stored separately."""
    return int.from_bytes(hashlib.sha256(remote_id.encode('ascii')).digest()[:8], 'big') & ((1<<63)-1)


@dataclass(frozen=True)
class YouTubeVideo:
    remote_id: str
    url: str
    published_at: datetime
    caption: str
    thumbnail_url: str
    duration: float | None
    views: int | None
    likes: int | None
    comments: int | None


def parse_video(item: dict[str, Any]) -> YouTubeVideo | None:
    video_id=item.get('id')
    if not isinstance(video_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id): return None
    sn=item.get('snippet') or {}
    when=sn.get('publishedAt')
    if not when:return None
    try: timestamp=datetime.fromisoformat(when.replace('Z','+00:00'))
    except (TypeError,ValueError):return None
    if timestamp.tzinfo is None: timestamp=timestamp.replace(tzinfo=timezone.utc)
    images=sn.get('thumbnails') or {}
    thumb=(images.get('medium') or images.get('default') or {}).get('url','')
    stats=item.get('statistics') or {}
    return YouTubeVideo(video_id, 'https://www.youtube.com/watch?v='+video_id, timestamp,
        ((sn.get('title') or '')+'\n'+(sn.get('description') or '')).strip()[:4000],
        thumb if isinstance(thumb,str) and thumb.startswith('https://') else '',
        duration_seconds((item.get('contentDetails') or {}).get('duration')),
        safe_metric(stats,'viewCount'),safe_metric(stats,'likeCount'),safe_metric(stats,'commentCount'))


class YouTubeClient:
    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None):
        if not api_key: raise YouTubeError('YOUTUBE_API_KEY отсутствует. Добавьте ключ через SETUP_PULSE.bat.')
        self.api_key=api_key
        self.transport=transport

    async def _get(self, client: httpx.AsyncClient, resource: str, params: dict) -> dict:
        try:
            response=await client.get(f'{API}/{resource}',params={'key':self.api_key,**params})
            if response.status_code != 200:
                code=response.status_code
                # Never log response.request.url: includes the API key.
                if code == 403: raise YouTubeError('YouTube API: нет доступа, квота исчерпана или ключ ограничен (403).')
                if code == 400: raise YouTubeError('YouTube API: неверные параметры или ключ (400).')
                raise YouTubeError(f'YouTube API HTTP {code}')
            data=response.json()
            if not isinstance(data,dict):raise YouTubeError('YouTube API: некорректный ответ')
            return data
        except httpx.TimeoutException as e: raise YouTubeError('YouTube API: превышено время ожидания') from e
        except httpx.TransportError as e: raise YouTubeError('YouTube API: нет соединения или проблема DNS') from e

    async def channel_and_videos(self, handle: str, channel_id: str | None, uploads_id: str | None,
                                 historical_ids: list[str], backfill_token: str | None = None,
                                 backfill_done: bool = False) -> tuple[dict, list[YouTubeVideo]]:
        kwargs={'timeout':httpx.Timeout(15, connect=8), 'follow_redirects':False}
        if self.transport is not None:kwargs['transport']=self.transport
        async with httpx.AsyncClient(**kwargs) as http:
            if not uploads_id:
                params={'part':'snippet,contentDetails'}
                if channel_id and channel_id.startswith('UC'):params['id']=channel_id
                elif handle.startswith('UC') and len(handle)==24:params['id']=handle
                else:params['forHandle']=handle.lstrip('@')
                data=await self._get(http,'channels',params)
                items=data.get('items') or []
                if not items:raise YouTubeError('YouTube-канал не найден. Проверьте @handle или Channel ID.')
                entry=items[0]
                channel_id=entry['id']
                uploads_id=(entry.get('contentDetails') or {}).get('relatedPlaylists',{}).get('uploads')
                if not uploads_id:raise YouTubeError('YouTube-канал не предоставляет список загрузок.')
                title=(entry.get('snippet') or {}).get('title') or handle
            else:
                title=None
            playlist=await self._get(http,'playlistItems',{'part':'contentDetails','playlistId':uploads_id,'maxResults':50})
            recent=[entry.get('contentDetails',{}).get('videoId') for entry in playlist.get('items',[])]
            # Incremental backfill: one older page per scan to avoid quota spikes.
            # API pageToken is stored between scans. New uploads are always checked.
            next_token=playlist.get('nextPageToken')
            archive=[]
            if not backfill_done and isinstance(backfill_token,str) and backfill_token and len(backfill_token)<300:
                backfill=await self._get(http,'playlistItems',{
                    'part':'contentDetails','playlistId':uploads_id,
                    'maxResults':50,'pageToken':backfill_token})
                archive=[item.get('contentDetails',{}).get('videoId') for item in backfill.get('items',[])]
                next_token=backfill.get('nextPageToken')
            done = not bool(next_token)
            unique=[]
            for vid in recent + archive + historical_ids[:50]:
                if isinstance(vid,str) and re.fullmatch(r'[A-Za-z0-9_-]{11}',vid) and vid not in unique:unique.append(vid)
            result=[]
            for i in range(0,len(unique),50):
                data=await self._get(http,'videos',{'part':'snippet,contentDetails,statistics','id':','.join(unique[i:i+50]),'maxResults':50})
                result.extend(v for item in data.get('items',[]) if (v:=parse_video(item)) is not None)
            return {'channel_id':channel_id,'uploads_id':uploads_id,'title':title,
                    'backfill_token': next_token if not done else None, 'backfill_done':done},result


async def get_public_video(api_key: str, video_id: str, transport: httpx.AsyncBaseTransport | None = None) -> tuple[YouTubeVideo, str, str]:
    """Return public video metadata and its channel; private/deleted content never guessed."""
    if not _VIDEO_ID.fullmatch(video_id):
        raise YouTubeError('Некорректный YouTube video ID.')
    client = YouTubeClient(api_key, transport=transport)
    opts = {'timeout': httpx.Timeout(15, connect=8), 'follow_redirects': False}
    if transport is not None:
        opts['transport'] = transport
    async with httpx.AsyncClient(**opts) as http:
        data = await client._get(http, 'videos', {'part':'snippet,contentDetails,statistics', 'id':video_id})
    items = data.get('items') or []
    if not items:
        raise YouTubeError('Видео недоступно через YouTube Data API (удалено, приватное или ограничено).')
    item = items[0]
    info = parse_video(item)
    snippet = item.get('snippet') or {}
    channel_id = snippet.get('channelId')
    if not info or not isinstance(channel_id, str) or not channel_id.startswith('UC') or len(channel_id) != 24:
        raise YouTubeError('YouTube вернул неполные метаданные.')
    return info, channel_id, str(snippet.get('channelTitle') or '')[:250]
