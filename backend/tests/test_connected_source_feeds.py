import httpx
import pytest
from app.integrations.connected_sources import (tiktok_self_feed,instagram_self_feed,ConnectedSourceError)

@pytest.mark.asyncio
async def test_tiktok_authorized_self_api():
    def handler(request):
        assert request.method=='POST'
        assert request.url.path=='/v2/video/list/'
        assert request.headers.get('authorization')=='Bearer TOKEN'
        assert request.url.params['fields'].startswith('id,title')
        return httpx.Response(200,json={'error':{'code':'ok'},'data':{'videos':[
            {'id':'753412341234','create_time':1700000000,'duration':41,'video_description':'My clip',
             'share_url':'https://www.tiktok.com/@me/video/753412341234','view_count':9000,'like_count':100,'comment_count':14}
        ]}})
    items=await tiktok_self_feed('TOKEN',transport=httpx.MockTransport(handler))
    assert len(items)==1 and items[0].views==9000
    assert items[0].likes==100 and items[0].duration==41


@pytest.mark.asyncio
async def test_tiktok_scope_denied():
    with pytest.raises(ConnectedSourceError,match='video.list'):
        await tiktok_self_feed('TOKEN',transport=httpx.MockTransport(lambda req:httpx.Response(403)))


@pytest.mark.asyncio
async def test_instagram_self_video_only():
    def handler(request):
        assert request.url.path.endswith('/987654321/media')
        assert request.headers['authorization']=='Bearer TOKEN'
        return httpx.Response(200,json={'data':[
            {'id':'video1','media_type':'VIDEO','timestamp':'2026-09-14T12:00:00+0000',
             'permalink':'https://www.instagram.com/reel/realid/','like_count':99,'comments_count':14},
            {'id':'image1','media_type':'IMAGE','timestamp':'2026-09-14T12:00:00+0000'}
        ]})
    videos=await instagram_self_feed('TOKEN','987654321',transport=httpx.MockTransport(handler))
    assert len(videos)==1 and videos[0].id=='video1'
    assert videos[0].views is None and videos[0].duration is None


def test_owner_oauth_source_contract():
    from app.integrations.sources import capability
    assert not capability('tiktok')['metadata_discovery']
    assert capability('tiktok',bound=True)['only_authorized_self_account']
    assert capability('instagram',bound=True)['metadata_discovery']
