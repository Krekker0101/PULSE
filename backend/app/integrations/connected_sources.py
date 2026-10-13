"""Authorized creator-account read-only feeds. No arbitrary account scraping."""
from datetime import datetime, timezone
from dataclasses import dataclass
import httpx
from .youtube import safe_metric
from ..config import get_settings

class ConnectedSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConnectedVideo:
    id: str
    url: str
    published_at: datetime
    title: str
    thumbnail_url: str
    duration: float | None
    views: int | None
    likes: int | None
    comments: int | None


def _timestamp(iso: str) -> datetime:
    dt=datetime.fromisoformat(iso.replace('Z','+00:00'))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def tiktok_self_feed(token: str, *, transport=None) -> list[ConnectedVideo]:
    url='https://open.tiktokapis.com/v2/video/list/'
    fields='id,title,video_description,create_time,duration,cover_image_url,share_url,view_count,like_count,comment_count'
    async with httpx.AsyncClient(timeout=20,transport=transport,follow_redirects=False) as client:
        try:
            resp=await client.post(url,params={'fields':fields},headers={'Authorization':'Bearer '+token},json={'max_count':20})
            if resp.status_code in (401,403):raise ConnectedSourceError('TikTok: требуется повторное подключение с разрешением video.list')
            if resp.status_code>=400:raise ConnectedSourceError(f'TikTok Display API: HTTP {resp.status_code}')
            data=resp.json()
            if (data.get('error') or {}).get('code') not in ('ok',None):
                raise ConnectedSourceError('TikTok Display API: ошибка разрешений или аккаунта')
            rows=(data.get('data') or {}).get('videos') or []
        except (httpx.TransportError,ValueError,KeyError) as e:
            raise ConnectedSourceError('Не удалось прочитать TikTok Display API') from e
    output=[]
    for v in rows:
        vid=str(v.get('id') or '')
        if not vid.isdigit() or not v.get('create_time'):continue
        url=v.get('share_url') or ''
        if not url.startswith(('https://www.tiktok.com/','https://tiktok.com/')):
            url=''  # Do not construct or follow speculative video URLs.
        thumb=v.get('cover_image_url') or ''
        try: when=datetime.fromtimestamp(int(v['create_time']),timezone.utc)
        except (ValueError,OverflowError):continue
        output.append(ConnectedVideo(vid,url,when,v.get('video_description') or v.get('title') or '',
            thumb if thumb.startswith('https://') else '',v.get('duration'),safe_metric(v,'view_count'),
            safe_metric(v,'like_count'),safe_metric(v,'comment_count')))
    return output


async def instagram_self_feed(token: str, user_id: str, *, transport=None) -> list[ConnectedVideo]:
    version=get_settings().instagram_graph_version
    # Credentials are for Instagram API with Instagram Login, not FB-Graph Page tokens.
    url=f'https://graph.instagram.com/{version}/{user_id}/media'
    async with httpx.AsyncClient(timeout=20,transport=transport,follow_redirects=False) as client:
        try:
            resp=await client.get(url,params={
                'fields':'id,caption,media_type,permalink,timestamp,thumbnail_url,like_count,comments_count',
                'limit':25},headers={'Authorization':'Bearer '+token})
            if resp.status_code in (401,403):raise ConnectedSourceError('Instagram: токен или разрешения недоступны')
            if resp.status_code>=400:raise ConnectedSourceError(f'Instagram API: HTTP {resp.status_code}')
            rows=resp.json().get('data') or []
        except (httpx.TransportError,ValueError,KeyError) as e:
            raise ConnectedSourceError('Не удалось прочитать Instagram API') from e
    output=[]
    for v in rows:
        if v.get('media_type')!='VIDEO' or not v.get('id') or not v.get('timestamp'):continue
        url=v.get('permalink') or ''
        if not url.startswith(('https://www.instagram.com/','https://instagram.com/')): url=''
        try:when=_timestamp(v['timestamp'])
        except (TypeError,ValueError):continue
        output.append(ConnectedVideo(str(v['id']),url,when,v.get('caption') or '',
                    v.get('thumbnail_url') or '',None,None,safe_metric(v,'like_count'),
                    safe_metric(v,'comments_count')))
    return output
