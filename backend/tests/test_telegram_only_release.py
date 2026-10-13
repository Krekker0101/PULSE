from pathlib import Path
import httpx
import pytest
from app.integrations.youtube import parse_youtube_video_url,get_public_video,YouTubeError
from app.config import Settings


@pytest.mark.parametrize('input_url,expected',[
 ('https://www.youtube.com/watch?v=Abc123_XyZ0','Abc123_XyZ0'),
 ('https://youtube.com/shorts/Abc123_XyZ0','Abc123_XyZ0'),
 ('https://youtu.be/Abc123_XyZ0','Abc123_XyZ0'),
 ('https://youtube.com/live/Abc123_XyZ0','Abc123_XyZ0'),
 ('https://www.youtube.com/watch?v=Abc123_XyZ0&t=1s','Abc123_XyZ0'),
 ('https://m.youtube.com/watch?v=Abc123_XyZ0','Abc123_XyZ0'),
])
def test_supported_youtube_urls(input_url,expected):
    assert parse_youtube_video_url(input_url)==expected


@pytest.mark.parametrize('bad',[
 'http://youtube.com/shorts/Abc123_XyZ0',
 'https://youtube.com.evil.com/watch?v=Abc123_XyZ0',
 'https://youtube.com@evil.com/shorts/Abc123_XyZ0',
 'https://youtube.com/watch?v=x',
 'https://www.instagram.com/reel/Abc123_XyZ0',
 'https://youtube.com/shorts/Abc123_XyZ0#fragment',
 'https://youtu.be/Abc123_XyZ0?redirect=https://evil.example',
 'javascript:alert(1)',
])
def test_reject_untrusted_video_links(bad):
    assert parse_youtube_video_url(bad) is None


@pytest.mark.asyncio
async def test_direct_youtube_video_metadata_only():
    def responder(request: httpx.Request):
        assert request.url.host=='www.googleapis.com'
        assert request.url.path == '/youtube/v3/videos'
        assert request.url.params['part']=='snippet,contentDetails,statistics'
        return httpx.Response(200,json={'items':[
            {'id':'Abc123_XyZ0',
             'snippet':{'publishedAt':'2026-10-09T10:00:00Z',
                        'channelId':'UC'+'z'*22,'channelTitle':'Creator', 'title':'My Short'},
             'contentDetails':{'duration':'PT45S'},
             'statistics':{'viewCount':'130000','likeCount':'1000','commentCount':'150'}}]})
    info,ch,title=await get_public_video('TEST_KEY','Abc123_XyZ0',httpx.MockTransport(responder))
    assert info.remote_id=='Abc123_XyZ0' and info.duration==45
    assert (info.views,info.likes,info.comments)==(130000,1000,150)
    assert ch=='UC'+'z'*22 and title=='Creator'
    assert info.url.endswith('Abc123_XyZ0')


@pytest.mark.asyncio
async def test_video_restricted_does_not_fake_result():
    async def responder(request):
        return httpx.Response(200,json={'items':[]})
    with pytest.raises(YouTubeError, match='недоступно'):
        await get_public_video('TEST_KEY','Abc123_XyZ0',httpx.MockTransport(responder))


def test_production_bot_only_needs_no_https_or_redis():
    from cryptography.fernet import Fernet
    settings=Settings(_env_file=None,environment='production',bot_only=True,
        bot_token='sample', owner_telegram_id=12345,
        database_url='postgresql+psycopg://u:p@db.example.com/pulse?sslmode=require',
        redis_url='',encryption_key=Fernet.generate_key().decode(),
        public_base_url='',development_api_key='')
    assert settings.bot_only
    assert settings.public_base_url==''


def test_cloud_bot_compose_never_starts_web_ui():
    import yaml
    path=Path(__file__).parents[2]/'docker-compose.telegram.yml'
    config=yaml.safe_load(path.read_text())
    assert 'pulse' in config['services']
    assert 'ports' not in config['services']['pulse']
    assert 'oauth-bridge' in config['services']
    assert config['services']['oauth-bridge']['profiles']==['social-oauth']
    assert not any('frontend' in x for x in config['services'])
    assert 'app.telegram_runtime' in config['services']['pulse']['command'][-1]

@pytest.mark.asyncio
async def test_youtube_incremental_history_without_full_archive_spike():
    from app.integrations.youtube import YouTubeClient
    calls=[]
    def responder(req:httpx.Request):
        calls.append((req.url.path,dict(req.url.params)))
        name=req.url.path.rsplit('/',1)[-1]
        if name=='channels':
            return httpx.Response(200,json={'items':[{'id':'UC'+'z'*22,
                'contentDetails':{'relatedPlaylists':{'uploads':'UU'+'z'*22}}}]})
        if name=='playlistItems':
            if req.url.params.get('pageToken')=='page2':
                return httpx.Response(200,json={'items':[{'contentDetails':{'videoId':'B'*11}}]})
            return httpx.Response(200,json={'items':[{'contentDetails':{'videoId':'A'*11}}],
                                            'nextPageToken':'page2'})
        if name=='videos':
            result=[]
            for v in req.url.params['id'].split(','):
                result.append({'id':v,'snippet':{'publishedAt':'2026-10-09T11:00:00Z','title':v},
                              'contentDetails':{'duration':'PT1M'},'statistics':{'viewCount':'10'}})
            return httpx.Response(200,json={'items':result})
        raise AssertionError('Non-official endpoint')
    yt=YouTubeClient('api-key',httpx.MockTransport(responder))
    state,vids=await yt.channel_and_videos('@example',None,None,[])
    assert state['backfill_token']=='page2' and not state['backfill_done']
    assert {v.remote_id for v in vids}=={'A'*11}
    state2,vids2=await yt.channel_and_videos('@example',state['channel_id'],state['uploads_id'],[],state['backfill_token'])
    assert state2['backfill_token'] is None and state2['backfill_done']
    assert {v.remote_id for v in vids2}=={'A'*11,'B'*11}
    assert len([x for x in calls if x[0].endswith('/playlistItems')])==3
