from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db import Base
from app.models import User, SocialAccount, Source, Video, Job, TelegramDestinationChat, utcnow
from app.services.destinations import telegram_permissions, connect_telegram, enqueue_destinations, DestinationError, check_account
from app.integrations.publishing import TelegramPublishingAdapter


@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


def seed(db):
    user = User(telegram_id=12345678); db.add(user); db.flush()
    src = Source(owner_id=user.id, username='own_upload', rights_status='user_attested', rights_evidence='I own this video', rights_instagram=True)
    db.add(src); db.flush()
    video = Video(owner_id=user.id, source_id=src.id, message_id=1, published_at=utcnow(), telegram_file_id='file123')
    db.add(video); db.flush()
    return user, video


def fake_bot(owner_status='creator'):
    return NS(get_chat=AsyncMock(return_value=NS(id=-100123, type='supergroup', title='My Group', username=None, is_forum=True, permissions=NS(can_send_messages=True, can_send_videos=True))),
              get_chat_member=AsyncMock(side_effect=[NS(status=owner_status), NS(status='administrator')]),
              get_me=AsyncMock(return_value=NS(id=999)))


@pytest.mark.parametrize('kind,status,post,expected', [('channel','member',False,False),('channel','administrator',False,False),('channel','administrator',True,True),('group','member',False,True),('supergroup','kicked',True,False)])
def test_permissions(kind, status, post, expected):
    chat = NS(type=kind, permissions=NS(can_send_messages=True, can_send_videos=True))
    assert telegram_permissions(chat, NS(status=status, can_post_messages=post)) == expected


def test_restricted_media_denied():
    assert not telegram_permissions(NS(type='supergroup'), NS(status='restricted', is_member=True, can_send_messages=True, can_send_videos=False))


@pytest.mark.asyncio
async def test_connect_private_forum_and_duplicate(db):
    user, _ = seed(db)
    a = await connect_telegram(db, fake_bot(), user, -100123, 42)
    assert a.connection_status == 'READY_TO_PUBLISH' and not a.publishing_enabled
    assert db.get(TelegramDestinationChat, a.id).message_thread_id == 42
    b = await connect_telegram(db, fake_bot(), user, -100123, 42)
    assert b.id == a.id and db.query(SocialAccount).count() == 1


@pytest.mark.asyncio
async def test_cannot_connect_foreign_chat(db):
    user, _ = seed(db)
    with pytest.raises(DestinationError): await connect_telegram(db, fake_bot('member'), user, -100123)
    assert db.query(SocialAccount).count() == 0


def test_multi_destination_enqueue_idempotency_and_ownership(db):
    user, video = seed(db)
    accounts = [SocialAccount(owner_id=user.id, provider='telegram', remote_id=str(n), encrypted_access_token='', publishing_enabled=True) for n in (-1001,-1002)]
    db.add_all(accounts); db.flush()
    jobs = enqueue_destinations(db, user.id, video.id, [a.id for a in accounts])
    assert len(jobs) == 2 and all(j.approved for j in jobs)
    enqueue_destinations(db, user.id, video.id, [a.id for a in accounts])
    assert db.query(Job).count() == 2
    with pytest.raises(DestinationError): enqueue_destinations(db, user.id + 1, video.id, [accounts[0].id])


def test_metadata_link_is_not_media(db):
    user, video = seed(db); video.telegram_file_id=None; video.source_url='https://youtube.com/watch?v=abcdefghijk'
    a=SocialAccount(owner_id=user.id,provider='telegram',remote_id='1',encrypted_access_token='',publishing_enabled=True)
    db.add(a); db.flush()
    with pytest.raises(DestinationError, match='метаданные'): enqueue_destinations(db,user.id,video.id,[a.id])
    assert db.query(Job).count()==0


@pytest.mark.asyncio
async def test_adapter_real_contract_file_id_and_topic():
    bot=NS(send_video=AsyncMock(return_value=NS(message_id=18,chat=NS(username='my_channel'),date=utcnow())))
    result=await TelegramPublishingAdapter(bot).dispatch(target=NS(chat_id=-1001,message_thread_id=42),file_id='file123',caption='<untrusted>')
    assert result['id']=='18' and result['url']=='https://t.me/my_channel/18'
    assert bot.send_video.call_args.kwargs['message_thread_id']==42
    assert bot.send_video.call_args.kwargs['parse_mode'] is None


@pytest.mark.asyncio
async def test_revoked_permissions_pause_destination(db):
    user, _=seed(db)
    a=await connect_telegram(db,fake_bot(),user,-100123)
    a.publishing_enabled=True
    assert await check_account(db,a,fake_bot('member'))=='NEEDS_ATTENTION'
    assert not a.publishing_enabled


@pytest.mark.asyncio
async def test_lost_telegram_response_is_never_retried(db, monkeypatch):
    from app.services import worker
    user,video=seed(db)
    a=await connect_telegram(db,fake_bot(),user,-100123); a.publishing_enabled=True
    job=enqueue_destinations(db,user.id,video.id,[a.id])[0]
    bot=fake_bot(); bot.session=NS(close=AsyncMock()); bot.send_video=AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr('aiogram.Bot',lambda *args,**kwargs:bot)
    await worker.telegram_step(db,job,video,a)
    assert job.status=='RECONCILIATION_REQUIRED'
    job.locked_until=utcnow()-timedelta(minutes=1); db.commit()
    assert worker.claim(db) is None
    assert bot.send_video.await_count==1


def test_ready_jobs_are_recovered(db):
    from app.services.worker import claim
    user,video=seed(db)
    a=SocialAccount(owner_id=user.id,provider='telegram',remote_id='1',encrypted_access_token='',publishing_enabled=True)
    db.add(a);db.flush()
    job=Job(owner_id=user.id,video_id=video.id,account_id=a.id,status='READY',approved=True)
    db.add(job);db.commit()
    assert claim(db)==job.id
    assert claim(db) is None


def test_manual_ready_never_claimed(db):
    from app.services.worker import claim
    user,video=seed(db)
    a=SocialAccount(owner_id=user.id,provider='telegram',remote_id='1',encrypted_access_token='')
    db.add(a);db.flush()
    db.add(Job(owner_id=user.id,video_id=video.id,account_id=a.id,status='READY',mode='MANUAL',approved=True))
    db.commit()
    assert claim(db) is None


def test_media_cleanup_protects_unfinished_jobs(db,tmp_path,monkeypatch):
    import os
    from app.services import media_retention
    user,video=seed(db)
    root=tmp_path/'originals';root.mkdir();path=root/'1.mp4';path.write_bytes(b'video')
    os.utime(path,(1,1));video.media_path=str(path)
    a=SocialAccount(owner_id=user.id,provider='telegram',remote_id='1',encrypted_access_token='',publishing_enabled=True)
    db.add(a);db.flush();job=enqueue_destinations(db,user.id,video.id,[a.id])[0]
    monkeypatch.setattr(media_retention,'get_settings',lambda:NS(storage_dir=tmp_path,media_retention_hours=48))
    assert media_retention.cleanup_media(db)==0 and path.exists()
    job.status='PUBLISHED';db.commit()
    assert media_retention.cleanup_media(db)==1 and not path.exists() and video.media_path is None


@pytest.mark.asyncio
async def test_rate_limit_reschedules_only_definite_rejection(db,monkeypatch):
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.methods import SendVideo
    from app.services import worker
    user,video=seed(db)
    a=await connect_telegram(db,fake_bot(),user,-100123);a.publishing_enabled=True
    job=enqueue_destinations(db,user.id,video.id,[a.id])[0]
    bot=fake_bot();bot.session=NS(close=AsyncMock())
    bot.send_video=AsyncMock(side_effect=TelegramRetryAfter(method=SendVideo(chat_id=-100123,video='file123'),message='slow down',retry_after=60))
    monkeypatch.setattr('aiogram.Bot',lambda *args,**kwargs:bot)
    await worker.telegram_step(db,job,video,a)
    assert job.status=='SCHEDULED'
    assert worker.claim(db) is None
