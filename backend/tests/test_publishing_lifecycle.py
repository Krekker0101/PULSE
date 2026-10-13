from unittest.mock import AsyncMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db import Base
from app.models import User, Source, Video, SocialAccount, Job, utcnow
from app.integrations.social import Instagram, TikTok, ProviderError
from app.services import worker


@pytest.fixture
def flow(tmp_path, monkeypatch):
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    factory=sessionmaker(bind=engine,expire_on_commit=False)
    monkeypatch.setattr(worker,'SessionLocal',factory)
    monkeypatch.setattr(worker,'worker_token',AsyncMock(return_value='token'))
    monkeypatch.setattr(worker.get_settings(),'public_base_url','https://pulse.example')
    monkeypatch.setattr('app.services.destinations.check_account',AsyncMock(return_value='READY_TO_PUBLISH'))
    monkeypatch.setattr('app.services.signed_media.public_url',lambda vid:'https://pulse.example/media/video.mp4')
    path=tmp_path/'own.mp4';path.write_bytes(b'mocked-media')
    with factory() as db:
        user=User(telegram_id=12345678);db.add(user);db.flush()
        source=Source(owner_id=user.id,username='owner-upload',rights_status='user_attested',rights_evidence='I own this original',rights_instagram=True,rights_tiktok=True)
        db.add(source);db.flush()
        video=Video(owner_id=user.id,source_id=source.id,message_id=1,published_at=utcnow(),telegram_file_id='file-id',media_path=str(path),media_info={'video_codec':'h264','format':'mp4','duration':15})
        db.add(video);db.flush()
        account=SocialAccount(owner_id=user.id,provider='instagram',remote_id='creator',encrypted_access_token='cipher',publishing_enabled=True)
        db.add(account);db.flush()
        job=Job(owner_id=user.id,video_id=video.id,account_id=account.id,approved=True,status='SCHEDULED')
        db.add(job);db.commit()
        yield db,job,account


@pytest.mark.asyncio
async def test_instagram_container_is_not_publication_and_verify_recovers(flow,monkeypatch):
    db,job,_=flow
    create=AsyncMock(return_value='container'); publish=AsyncMock(return_value={'id':'media'})
    media=AsyncMock(side_effect=[ProviderError('temporary verification failure'),{'id':'media','permalink':'https://instagram.com/reel/example'}])
    monkeypatch.setattr(Instagram,'create_reel_container',create)
    monkeypatch.setattr(Instagram,'container_status',AsyncMock(return_value={'status_code':'FINISHED'}))
    monkeypatch.setattr(Instagram,'publish_container',publish)
    monkeypatch.setattr(Instagram,'media_info',media)
    await worker.step(job.id);db.expire_all();db.refresh(job)
    assert job.status=='PLATFORM_PROCESSING' and job.platform_id=='container'
    publish.assert_not_awaited()
    await worker.step(job.id);db.expire_all();db.refresh(job)
    assert job.status=='VERIFYING' and job.platform_id=='media'
    await worker.step(job.id);db.expire_all();db.refresh(job)
    assert job.status=='PUBLISHED' and job.platform_url.startswith('https://instagram.com/')
    assert create.await_count==publish.await_count==1


@pytest.mark.asyncio
async def test_instagram_lost_publish_reply_never_claimed_again(flow,monkeypatch):
    db,job,_=flow;job.status='PLATFORM_PROCESSING';job.platform_id='container';db.commit()
    monkeypatch.setattr(Instagram,'container_status',AsyncMock(return_value={'status_code':'FINISHED'}))
    publish=AsyncMock(side_effect=TimeoutError);monkeypatch.setattr(Instagram,'publish_container',publish)
    await worker.step(job.id);db.expire_all();db.refresh(job)
    assert job.status=='RECONCILIATION_REQUIRED'
    assert worker.claim(db) is None and publish.await_count==1


@pytest.mark.asyncio
async def test_tiktok_inbox_delivery_is_draft_not_published(flow,monkeypatch):
    db,job,account=flow
    account.provider='tiktok';job.mode='DRAFT';job.status='PLATFORM_PROCESSING';job.platform_id='publish-id';db.commit()
    cfg=worker.get_settings();monkeypatch.setattr(cfg,'tiktok_verified_media_domain',True)
    monkeypatch.setattr(TikTok,'status',AsyncMock(return_value={'status':'SEND_TO_USER_INBOX'}))
    await worker.step(job.id);db.expire_all();db.refresh(job)
    assert job.status=='DRAFT_CREATED'


@pytest.mark.asyncio
async def test_paused_destination_keeps_job_for_resume(flow):
    db,job,account=flow;account.publishing_enabled=False;db.commit()
    await worker.step(job.id);db.expire_all();db.refresh(job)
    assert job.status=='SCHEDULED' and job.scheduled_at is not None
