"""Official API adapters. No silent retry on ambiguous publish outcomes."""
import httpx
from ..config import get_settings


class ProviderError(Exception):
    pass


class ProviderTransportError(ProviderError):
    """Request outcome may be unknown; never expose request URLs in logs."""
    pass


async def request_json(method: str, url: str, token: str | None = None, **kwargs) -> dict:
    headers = dict(kwargs.pop('headers', {}))
    if token: headers['Authorization'] = f'Bearer {token}'
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.request(method, url, headers=headers, **kwargs)
    except httpx.HTTPError:
        raise ProviderTransportError('Platform connection failed; result may be uncertain') from None
    try: data = response.json()
    except ValueError: raise ProviderError(f'Platform returned HTTP {response.status_code} with invalid JSON') from None
    if not isinstance(data, dict):
        raise ProviderError('Platform returned an unexpected response')
    error = data.get('error')
    if response.status_code >= 400 or (isinstance(error, dict) and error.get('code') not in (None, 'ok')) or isinstance(error, str) and error:
        # Provider error messages may echo credentials or media URLs. Never persist them.
        code = error.get('code', 'unknown') if isinstance(error, dict) else 'oauth_error'
        raise ProviderError(f'Platform rejected request HTTP {response.status_code} code={code}')
    return data


class Instagram:
    @staticmethod
    def graph(path: str) -> str:
        return f'https://graph.instagram.com/{get_settings().instagram_graph_version}/{path}'

    async def create_reel_container(self, token: str, user_id: str, url: str, caption: str) -> str:
        if not url.startswith('https://'): raise ProviderError('Instagram needs a reachable HTTPS media URL')
        data = await request_json('POST', self.graph(f'{user_id}/media'), token, data={
            'media_type': 'REELS', 'video_url': url, 'caption': caption[:2200]})
        if 'id' not in data: raise ProviderError('Instagram did not return container ID')
        return str(data['id'])

    async def container_status(self, token: str, container_id: str):
        return await request_json('GET', self.graph(container_id), token, params={'fields': 'status_code,status'})

    async def publish_container(self, token: str, user_id: str, container_id: str):
        return await request_json('POST', self.graph(f'{user_id}/media_publish'), token,
                                  data={'creation_id': container_id})

    async def media_info(self, token: str, media_id: str):
        return await request_json('GET', self.graph(media_id), token, params={'fields': 'id,permalink'})


class TikTok:
    root = 'https://open.tiktokapis.com/v2'

    async def creator_info(self, token):
        data = await request_json('POST', self.root+'/post/publish/creator_info/query/', token,
                                  json={})
        return data['data']

    async def status(self, token, publish_id):
        data = await request_json('POST', self.root+'/post/publish/status/fetch/', token,
                                  json={'publish_id': publish_id})
        return data['data']

    async def initialize_draft_from_verified_url(self, token, url):
        if not url.startswith('https://'): raise ProviderError('Verified HTTPS domain required')
        data = await request_json('POST', self.root+'/post/publish/inbox/video/init/', token,
                                  json={'source_info': {'source': 'PULL_FROM_URL', 'video_url': url}})
        return data['data']['publish_id']

    async def initialize_direct_post(self, token, url, title, privacy_level, creator_info,
                                     consent: bool):
        if not consent: raise ProviderError('Explicit user consent required')
        if privacy_level not in creator_info.get('privacy_level_options', []):
            raise ProviderError('Unsupported privacy level')
        if not url.startswith('https://'): raise ProviderError('Verified HTTPS domain required')
        data = await request_json('POST', self.root+'/post/publish/video/init/', token, json={
            'post_info': {'privacy_level': privacy_level, 'title': title[:2200],
                          'disable_duet': False, 'disable_comment': False, 'disable_stitch': False},
            'source_info': {'source': 'PULL_FROM_URL', 'video_url': url}})
        return data['data']['publish_id']
