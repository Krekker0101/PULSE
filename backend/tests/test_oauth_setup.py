from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import importlib.util
from cryptography.fernet import Fernet
from dotenv import dotenv_values
import pytest
from app.services.oauth import configuration_issues, setup_message


def cfg(**changes):
    values=dict(instagram_app_id='123',instagram_app_secret='private-secret',tiktok_client_key='key',
                tiktok_client_secret='private-tiktok',encryption_key=Fernet.generate_key().decode(),public_base_url='https://pulse.example.com')
    return NS(**{**values,**changes})


def test_reports_exact_missing_provider_keys_without_secret_values():
    issues=configuration_issues(cfg(instagram_app_id='',instagram_app_secret=''), 'instagram')
    assert any('INSTAGRAM_APP_ID' in i for i in issues)
    assert any('INSTAGRAM_APP_SECRET' in i for i in issues)
    assert not any('TIKTOK' in i for i in issues)
    assert 'private-tiktok' not in setup_message('instagram',issues)


@pytest.mark.parametrize('url',['http://127.0.0.1:8000','https://localhost','https://127.0.0.1',
                              'https://user:secret@pulse.example.com','https://pulse.example.com/path',
                              'https://pulse.example.com?token=secret','https://pulse.example.com#fragment'])
def test_callback_requires_public_https_base(url):
    assert any('PUBLIC_BASE_URL' in i for i in configuration_issues(cfg(public_base_url=url),'tiktok'))


def test_valid_provider_is_independent_of_other_missing_credentials():
    assert configuration_issues(cfg(tiktok_client_key='',tiktok_client_secret=''),'instagram')==[]
    assert any('ENCRYPTION_KEY' in i for i in configuration_issues(cfg(encryption_key='invalid'),'instagram'))


def local_module():
    path=Path(__file__).parents[2]/'scripts'/'pulse_local.py'
    spec=importlib.util.spec_from_file_location('pulse_oauth_setup',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_env_updates_preserve_database_and_existing_secrets(tmp_path,monkeypatch):
    pulse=local_module();monkeypatch.setattr(pulse,'BACKEND',tmp_path)
    target=tmp_path/'.env'
    target.write_text('# existing settings\nDATABASE_URL="postgresql://keep"\nBOT_TOKEN="keep-token"\nENCRYPTION_KEY="keep-key"\nPUBLIC_BASE_URL="http://localhost"\n',encoding='utf-8')
    pulse.save_env_updates({'PUBLIC_BASE_URL':'https://pulse.example.com','INSTAGRAM_APP_SECRET':"a#b'c"})
    values=dotenv_values(target)
    assert values['DATABASE_URL']=='postgresql://keep' and values['BOT_TOKEN']=='keep-token'
    assert values['ENCRYPTION_KEY']=='keep-key' and values['INSTAGRAM_APP_SECRET']=="a#b'c"
    assert target.read_text(encoding='utf-8').startswith('# existing settings')
    assert not (tmp_path/'.env.oauth.new').exists()


def test_invalid_wizard_does_not_write_env(tmp_path,monkeypatch):
    pulse=local_module();monkeypatch.setattr(pulse,'BACKEND',tmp_path)
    target=tmp_path/'.env';original='BOT_TOKEN="keep"\n'
    target.write_text(original,encoding='utf-8')
    answers=iter(['1','http://localhost'])
    monkeypatch.setattr('builtins.input',lambda *_:next(answers))
    monkeypatch.setattr(pulse,'secret',lambda *args:'')
    with pytest.raises(ValueError):pulse.oauth_setup()
    assert target.read_text(encoding='utf-8')==original


@pytest.mark.asyncio
async def test_database_failure_not_misreported_as_missing_oauth_keys(monkeypatch):
    from app import bot
    from contextlib import nullcontext
    monkeypatch.setattr(bot,'owner_ok',lambda uid:True)
    monkeypatch.setattr(bot,'SessionLocal',lambda:nullcontext(NS()))
    monkeypatch.setattr(bot,'user_db',lambda db:NS(id=1))
    def fail(*args):raise RuntimeError('private database error')
    monkeypatch.setattr(bot,'begin',fail)
    screen=AsyncMock();monkeypatch.setattr(bot,'screen',screen)
    await bot.oauth_link(NS(from_user=NS(id=1),data='oauth:instagram'))
    text=screen.call_args.args[1]
    assert 'БД' in text and 'OAuth-ключи' not in text and 'private database error' not in text
