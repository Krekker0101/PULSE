from pathlib import Path
import pytest
from sqlalchemy import create_engine, text, inspect
from alembic.config import Config
from alembic import command
from app.db import Base
from app.config import get_settings


@pytest.mark.parametrize('revision', ['0001_initial', '0004_destinations'])
def test_partial_schema_repaired_even_when_platform_already_exists(tmp_path, monkeypatch, revision):
    url = 'sqlite:///' + str(tmp_path / 'partial.db')
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import Session
    from app.models import User, Source, Video, utcnow
    with Session(engine) as db:
        user = User(telegram_id=42); db.add(user); db.flush()
        source = Source(owner_id=user.id, username='own', platform='telegram'); db.add(source); db.flush()
        db.add(Video(owner_id=user.id, source_id=source.id, message_id=8,
                     telegram_file_id='original_file', original_caption='Keep caption', published_at=utcnow()))
        db.commit()
    with engine.begin() as con:
        for name in ('remote_video_id', 'source_url', 'thumbnail_url'):
            con.execute(text('ALTER TABLE videos DROP COLUMN ' + name))
    cfg = get_settings()
    monkeypatch.setattr(cfg, 'database_url', url)
    monkeypatch.setattr(cfg, 'database_url_unpooled', '')
    root = Path(__file__).parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'alembic'))
    command.stamp(config, revision)
    command.upgrade(config, 'head')
    command.upgrade(config, 'head')
    with engine.connect() as con:
        assert {'remote_video_id', 'source_url', 'thumbnail_url'} <= {c['name'] for c in inspect(con).get_columns('videos')}
        assert con.execute(text('SELECT telegram_file_id, original_caption FROM videos WHERE id=1')).one() == ('original_file', 'Keep caption')
        assert con.execute(text('SELECT version_num FROM alembic_version')).scalar() == '0005_repair_schema'


def test_local_start_migrates_before_launch_and_aborts_on_failure(monkeypatch):
    import importlib.util
    import subprocess
    path = Path(__file__).parents[2] / 'scripts' / 'pulse_local.py'
    spec = importlib.util.spec_from_file_location('pulse_local_repair', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_processes', lambda: None)
    monkeypatch.setattr(module, 'check', lambda: True)
    called = []
    def fail(argv, **kwargs):
        called.append(argv)
        raise subprocess.CalledProcessError(1, argv)
    monkeypatch.setattr(module, 'exec_checked', fail)
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('Services launched before migration succeeded'))
    with pytest.raises(subprocess.CalledProcessError): module.start()
    assert called[0][-4:] == ['-m', 'alembic', 'upgrade', 'head']
