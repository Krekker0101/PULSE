import hashlib
from datetime import timedelta
from types import SimpleNamespace as NS
from urllib.parse import urlparse, parse_qs
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db import Base
from app.models import User, OAuthFlow, utcnow
from app.services import oauth


@pytest.fixture
def setup(monkeypatch):
    cfg=NS(owner_telegram_id=42,instagram_app_id='ig',instagram_app_secret='secret',
           instagram_graph_version='v23.0',tiktok_client_key='tk',tiktok_client_secret='secret',
           public_base_url='https://pulse.example',tiktok_discovery_enabled=False,
           encryption_key=Fernet.generate_key().decode())
    monkeypatch.setattr(oauth,'get_settings',lambda:cfg)
    monkeypatch.setattr('app.security.get_settings',lambda:cfg)
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as db:
        user=User(telegram_id=42);db.add(user);db.commit()
        yield db,user,cfg


def test_begin_state_is_hashed_and_bound_to_owner(setup):
    db,user,_=setup
    url=oauth.begin(db,user.id,'tiktok')
    state=parse_qs(urlparse(url).query)['state'][0]
    flow=db.query(OAuthFlow).one()
    assert flow.state_hash==hashlib.sha256(state.encode()).hexdigest()
    assert flow.owner_id==user.id and not flow.consumed
    foreign=User(telegram_id=99);db.add(foreign);db.commit()
    with pytest.raises(HTTPException) as exc:oauth.begin(db,foreign.id,'instagram')
    assert exc.value.status_code==403


@pytest.mark.asyncio
async def test_tiktok_callback_granted_scope_and_replay(setup,monkeypatch):
    db,user,_=setup
    state=parse_qs(urlparse(oauth.begin(db,user.id,'tiktok')).query)['state'][0]
    mock=AsyncMock(side_effect=[{'access_token':'access','refresh_token':'refresh','open_id':'id','scope':'user.info.basic','expires_in':86400},
                               {'data':{'user':{'open_id':'id','display_name':'creator'}}}])
    monkeypatch.setattr(oauth,'request_json',mock)
    account=await oauth.complete(db,'tiktok',state,'code')
    assert not account.capabilities['draft'] and not account.publishing_enabled
    assert account.encrypted_access_token!='access'
    with pytest.raises(HTTPException):await oauth.complete(db,'tiktok',state,'code')
    assert mock.await_count==2


@pytest.mark.asyncio
async def test_expired_state_rejected_without_exchange(setup,monkeypatch):
    db,user,_=setup
    state=parse_qs(urlparse(oauth.begin(db,user.id,'instagram')).query)['state'][0]
    flow=db.query(OAuthFlow).one();flow.expires_at=utcnow()-timedelta(seconds=1);db.commit()
    mock=AsyncMock();monkeypatch.setattr(oauth,'request_json',mock)
    with pytest.raises(HTTPException):await oauth.complete(db,'instagram',state,'code')
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_instagram_permissions_not_assumed(setup,monkeypatch):
    db,user,_=setup
    state=parse_qs(urlparse(oauth.begin(db,user.id,'instagram')).query)['state'][0]
    mock=AsyncMock(side_effect=[{'access_token':'short'}, {'access_token':'long','expires_in':5184000},
                               {'id':'ig_id','username':'creator','account_type':'BUSINESS'},
                               {'data':[{'permission':'instagram_business_basic','status':'granted'}]}])
    monkeypatch.setattr(oauth,'request_json',mock)
    account=await oauth.complete(db,'instagram',state,'code')
    assert not account.capabilities['reels']
    assert 'instagram_business_content_publish' not in account.scopes


@pytest.mark.asyncio
async def test_owner_only_callbacks_do_not_touch_database(monkeypatch):
    from app import telegram_accounts
    cb=NS(from_user=NS(id=99),answer=AsyncMock(),data='pubconfirm')
    monkeypatch.setattr(telegram_accounts,'owner_ok',lambda uid:uid==42)
    state=NS(get_data=AsyncMock())
    await telegram_accounts.publish_confirm(cb,state)
    state.get_data.assert_not_awaited()
    cb.answer.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['accounts_page','add_chat','check_connections','caption_start',
                                 'publish_picker','publish_select','advanced_schedule','priority',
                                 'route_mode','document_mode','account_history'])
async def test_all_account_callbacks_reject_foreign_user(name,monkeypatch):
    import inspect
    from app import telegram_accounts
    cb=NS(from_user=NS(id=99),answer=AsyncMock(),data='malformed')
    monkeypatch.setattr(telegram_accounts,'owner_ok',lambda uid:uid==42)
    handler=getattr(telegram_accounts,name)
    args=[cb]
    if 'state' in inspect.signature(handler).parameters:args.append(NS())
    await handler(*args)
    cb.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_clears_account_wizard_before_text_handler(monkeypatch):
    from app import telegram_accounts, bot
    monkeypatch.setattr(telegram_accounts,'owner_ok',lambda uid:uid==42)
    start=AsyncMock();monkeypatch.setattr(bot,'start',start)
    message=NS(from_user=NS(id=42),chat=NS(type='private'),answer=AsyncMock(),text='/cancel')
    state=NS(clear=AsyncMock())
    await telegram_accounts.reset_dialog(message,state)
    state.clear.assert_awaited_once()
    start.assert_awaited_once_with(message)


def test_migration_preserves_old_account_and_is_repeatable(tmp_path):
    from pathlib import Path
    from sqlalchemy import text, inspect
    import importlib.util
    root=Path(__file__).parents[1]
    engine=create_engine('sqlite:///'+str(tmp_path/'migration.db'))
    Base.metadata.create_all(engine)
    with engine.begin() as con:
        con.execute(text("INSERT INTO users (id, telegram_id, display_name, timezone, monitoring_enabled, notify_enabled, created_at) VALUES (1,42,'Owner','Asia/Dushanbe',1,1,CURRENT_TIMESTAMP)"))
        con.execute(text("INSERT INTO social_accounts (id,owner_id,provider,remote_id,username,encrypted_access_token,scopes,enabled,created_at) VALUES (1,1,'instagram','old','old','cipher','basic',1,CURRENT_TIMESTAMP)"))
        # Reconstruct the old table's column set to exercise the upgrade path.
        for name in ('last_verified_at','settings','capabilities','publishing_enabled','connection_status'):
            con.execute(text('ALTER TABLE social_accounts DROP COLUMN '+name))
        con.execute(text('DROP TABLE telegram_destination_chats'))
        con.execute(text('DROP TABLE publishing_attempts'))
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path=root/'alembic'/'versions'/'0004_publishing_destinations.py'
    spec=importlib.util.spec_from_file_location('migration',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    with engine.begin() as con:
        with Operations.context(MigrationContext.configure(con)):
            mod.upgrade();mod.upgrade()
        assert con.execute(text('SELECT encrypted_access_token FROM social_accounts WHERE id=1')).scalar()=='cipher'
        assert 'settings' in {c['name'] for c in inspect(con).get_columns('social_accounts')}


def test_clean_alembic_upgrade_twice(tmp_path,monkeypatch):
    from alembic.config import Config
    from alembic import command
    from pathlib import Path
    from app.config import get_settings
    cfg=get_settings();monkeypatch.setattr(cfg,'database_url','sqlite:///'+str(tmp_path/'clean.db'))
    root=Path(__file__).parents[1]
    config=Config(str(root/'alembic.ini'));config.set_main_option('script_location',str(root/'alembic'))
    command.upgrade(config,'head');command.upgrade(config,'head')
