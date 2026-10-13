import os
os.environ['ENVIRONMENT']='development'
os.environ['DATABASE_URL']='sqlite:///:memory:'
os.environ['OWNER_TELEGRAM_ID']='12345678'
os.environ['DEVELOPMENT_API_KEY']='test-key-of-sufficient-entropy'
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import Base,get_db
from app.api import app

engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
Base.metadata.create_all(engine)
Session=sessionmaker(bind=engine)

def override():
    with Session() as db: yield db

app.dependency_overrides[get_db]=override
client=TestClient(app)
headers={'X-Pulse-Dev-Key':'test-key-of-sufficient-entropy'}


def test_owner_auth_required():
    assert client.get('/api/dashboard').status_code==401
    assert client.get('/api/dashboard',headers=headers).status_code==200


def test_source_crud_ownership():
    response=client.post('/api/sources',json={'username':'@valid_channel'},headers=headers)
    assert response.status_code==201,response.text
    id=response.json()['id']
    assert client.post('/api/sources',json={'username':'@valid_channel'},headers=headers).status_code==409
    assert client.patch(f'/api/sources/{id}',json={'min_views':40000,'format_mode':'SHORT'},headers=headers).status_code==200
    bad=client.patch(f'/api/sources/{id}',json={'filter_tree':{'op':'>=','field':'not_a_field','value':2}},headers=headers)
    assert bad.status_code==422
    assert client.delete(f'/api/sources/{id}',headers=headers).status_code==200
    assert client.get('/api/sources',headers=headers).json()[0]['enabled'] is False


def test_tiktok_routing_rejects_noncompliant_auto_and_approval():
    """Creating a misleading route must fail before the user can enable publishing."""
    from app.models import SocialAccount,User
    source = client.post('/api/sources',json={'username':'route_policy_channel'},headers=headers)
    assert source.status_code == 201, source.text
    source_id = source.json()['id']
    with Session() as db:
        owner=db.query(User).filter_by(telegram_id=12345678).one()
        account=SocialAccount(owner_id=owner.id,provider='tiktok',remote_id='stub_account',
                              username='stub',encrypted_access_token='test_unusable',enabled=True)
        db.add(account);db.commit();account_id=account.id
    for mode in ['AUTO','APPROVAL']:
        response=client.post('/api/routes',json={'source_id':source_id,'account_id':account_id,'mode':mode},headers=headers)
        assert response.status_code == 409, response.text
    permitted=client.post('/api/routes',json={'source_id':source_id,'account_id':account_id,'mode':'DRAFT'},headers=headers)
    assert permitted.status_code==201, permitted.text
    assert permitted.json()['mode']=='DRAFT'
    repeated=client.post('/api/routes',json={'source_id':source_id,'account_id':account_id,'mode':'DRAFT'},headers=headers)
    assert repeated.status_code==409


def test_youtube_source_requires_owner_and_has_real_capabilities():
    new={'username':'https://www.youtube.com/@PulseTestChannel','format_mode':'SHORT'}
    assert client.post('/api/sources',json=new).status_code==401
    response=client.post('/api/sources',json=new,headers=headers)
    assert response.status_code==201,response.text
    data=response.json()
    assert data['platform']=='youtube'
    assert data['url']=='https://www.youtube.com/@pulsetestchannel'
    assert data['format_mode']=='SHORT'
    assert data['capabilities']['metadata_discovery'] is True
    assert data['capabilities']['media_download'] is False
    assert data['access_status']=='awaiting_api_key'
    assert client.get('/api/sources',headers=headers).status_code==200
    assert client.get('/api/source-capabilities',headers=headers).status_code==200
    # A YouTube source is never allowed to enter automatic upload by URL.
    from app.models import SocialAccount,User
    with Session() as db:
        owner=db.query(User).filter_by(telegram_id=12345678).one()
        account=SocialAccount(owner_id=owner.id,provider='instagram',remote_id='another-stub',
                              username='stub',encrypted_access_token='test_unusable',enabled=True)
        db.add(account);db.commit();account_id=account.id
    for mode in ('AUTO','APPROVAL','DRAFT'):
        r=client.post('/api/routes',json={'source_id':data['id'],'account_id':account_id,'mode':mode},headers=headers)
        assert r.status_code==409,r.text
    r=client.post('/api/routes',json={'source_id':data['id'],'account_id':account_id,'mode':'MANUAL'},headers=headers)
    assert r.status_code==201,r.text


def test_youtube_missing_api_key_is_honest_error():
    response=client.post('/api/sources',json={'username':'https://youtube.com/@another_source'},headers=headers)
    assert response.status_code==201,response.text
    video_url=f"/api/sources/{response.json()['id']}/scan"
    assert client.post(video_url).status_code==401
    attempted=client.post(video_url,headers=headers)
    assert attempted.status_code==503,attempted.text
    assert 'YOUTUBE_API_KEY' in attempted.text


def test_self_account_binding_cannot_cross_providers():
    response=client.post('/api/sources',json={'username':'https://instagram.com/some_creator/'},headers=headers)
    assert response.status_code==201,response.text
    source_id=response.json()['id']
    from app.models import SocialAccount,User
    with Session() as db:
        owner=db.query(User).filter_by(telegram_id=12345678).one()
        foreign=SocialAccount(owner_id=owner.id,provider='tiktok',remote_id='not-instagram',
                              username='some_creator',encrypted_access_token='unusable',enabled=True)
        db.add(foreign);db.commit();account_id=foreign.id
    assert client.put(f'/api/sources/{source_id}/connection',json={'account_id':account_id},headers=headers).status_code==404
    assert client.put(f'/api/sources/{source_id}/connection',json={'account_id':None},headers=headers).status_code==200
