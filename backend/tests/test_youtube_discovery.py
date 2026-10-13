import httpx
import pytest
from app.integrations.youtube import (YouTubeClient,YouTubeError,parse_video,duration_seconds,
                                     message_id_from_remote_id)

VID='abcdefghijk'

def make_video(views='150000',likes='2000',comments='21'):
    return {'id':VID,'snippet':{'publishedAt':'2026-10-09T10:00:00Z','title':'Hello','description':'world',
             'thumbnails':{'medium':{'url':'https://img.youtube.com/sample.jpg'}}},
           'contentDetails':{'duration':'PT1M12S'},
           'statistics':{'viewCount':views,'likeCount':likes,'commentCount':comments}}


def test_parse_video_metrics():
    item=parse_video(make_video())
    assert item.remote_id==VID and item.views==150000
    assert item.likes==2000 and item.comments==21
    assert item.duration==72.0 and item.published_at.tzinfo is not None
    assert item.url=='https://www.youtube.com/watch?v='+VID
    assert parse_video({}) is None
    assert parse_video({**make_video(), 'statistics':{}}).views is None
    assert message_id_from_remote_id(VID)==message_id_from_remote_id(VID)
    assert 0<=message_id_from_remote_id(VID)<2**63


@pytest.mark.parametrize('duration,seconds', [('PT30S',30),('PT3M',180),('PT2H3M4S',7384),('P1DT2H',93600),('garbage',None)])
def test_duration(duration,seconds):
    assert duration_seconds(duration)==seconds


@pytest.mark.asyncio
async def test_youtube_official_api_scan_no_file_download():
    calls=[]
    def responder(request:httpx.Request):
        calls.append((request.url.path,dict(request.url.params)))
        assert request.url.host=='www.googleapis.com'
        assert request.url.params['key']=='example-key'
        if request.url.path.endswith('/channels'):
            assert request.url.params['forHandle']=='example'
            result={'items':[{'id':'UC'+'A'*22,'snippet':{'title':'Example'},
                     'contentDetails':{'relatedPlaylists':{'uploads':'UU'+'A'*22}}}]}
        elif request.url.path.endswith('/playlistItems'):
            result={'items':[{'contentDetails':{'videoId':VID}}]}
        elif request.url.path.endswith('/videos'):
            result={'items':[make_video()]}
        else:raise AssertionError('Nonofficial API endpoint')
        return httpx.Response(200,json=result)
    client=YouTubeClient('example-key',httpx.MockTransport(responder))
    channel,videos=await client.channel_and_videos('example',None,None,[])
    assert channel['channel_id']=='UC'+'A'*22
    assert channel['uploads_id']=='UU'+'A'*22
    assert len(videos)==1 and videos[0].views==150000
    assert len(calls)==3
    assert all('download' not in path.lower() for path,_ in calls)


@pytest.mark.asyncio
async def test_youtube_quota_error_does_not_leak_key():
    client=YouTubeClient('TOP_SECRET_KEY',httpx.MockTransport(lambda _:httpx.Response(403,json={'error':'quotaExceeded'})))
    with pytest.raises(YouTubeError) as err:
        await client.channel_and_videos('test',None,None,[])
    assert 'TOP_SECRET_KEY' not in str(err.value)
    assert '403' in str(err.value)
