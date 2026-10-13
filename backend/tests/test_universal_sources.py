import pytest
from app.integrations.sources import normalize_public_source,capability

@pytest.mark.parametrize('value,provider,label',[
    ('https://www.youtube.com/@ExampleChannel','youtube','examplechannel'),
    ('https://www.youtube.com/@ExampleChannel/shorts','youtube','examplechannel'),
    ('https://www.youtube.com/@ExampleChannel/videos','youtube','examplechannel'),
    ('https://www.instagram.com/creator_name/reels/','instagram','creator_name'),
    ('@GoogleDevelopers','youtube','googledevelopers'),
    ('https://www.youtube.com/channel/UCAAAAAAAAAAAAAAAAAAAAAA','youtube','UCAAAAAAAAAAAAAAAAAAAAAA'),
    ('https://t.me/pulse_owner','telegram','pulse_owner'),
    ('pulse_owner','telegram','pulse_owner'),
    ('tg:@pulse_owner','telegram','pulse_owner'),
    ('https://www.instagram.com/creator_name/','instagram','creator_name'),
    ('https://www.tiktok.com/@creator_name','tiktok','creator_name'),
    ('https://facebook.com/creator_name','facebook','creator_name'),
    ('https://vk.com/pulsegroup','vk','pulsegroup'),
    ('https://rutube.ru/channel/12345/','rutube','12345'),
    ('https://x.com/someone','x','someone'),
    ('https://www.twitch.tv/streamer','twitch','streamer'),
    ('https://bsky.app/profile/someone.bsky.social','bluesky','someone.bsky.social'),
])
def test_known_providers(value,provider,label):
    item=normalize_public_source(value)
    assert item.provider==provider
    assert item.label==label
    assert item.url.startswith('https://')
    assert item.status


@pytest.mark.parametrize('url',[
    'https://evil.example.com/@creator',
    'http://youtube.com/@creator',
    'https://youtube.com/watch?v=abcdefghijk',
    'https://youtube.com/shorts/abcdefghijk',
    'https://youtube.com/c/creator',
    'https://www.tiktok.com/@creator/video/123456',
    'https://www.youtube.com.evil.example/@creator',
    'https://youtube.com/@creator?evil=1',
    'https://youtube.com@evil.example/@creator',
    'javascript:alert(1)',
])
def test_invalid_untrusted_or_not_channel(url):
    with pytest.raises(ValueError):normalize_public_source(url)


def test_capability_is_honest():
    assert capability('youtube')['metadata_discovery']
    assert not capability('youtube')['media_download']
    assert not capability('tiktok')['metadata_discovery']
    assert capability('telegram')['media_download']
