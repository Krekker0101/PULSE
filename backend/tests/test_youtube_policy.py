from datetime import datetime,timezone,timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db import Base
from app.models import User,Source,Video,Metric
from app.services.catalog import ingest
from app.services.youtube_retention import cleanup


def test_youtube_does_not_derive_viral_score_and_expired_metadata_clears():
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    now=datetime.now(timezone.utc)
    with Session(engine) as db:
        user=User(telegram_id=432343)
        db.add(user);db.flush()
        s=Source(owner_id=user.id,username='youtube:UC'+'a'*22,platform='youtube',
                 min_views=100,max_age_hours=None,access_status='youtube_api_active')
        db.add(s);db.flush()
        item,created=ingest(db,s,1,now,duration=55,caption='Test metadata',views=100000,
                             reactions=1000,replies=150,remote_video_id='A'*11)
        assert created and item.viral_score is None and item.eligible
        assert item.metadata_checked_at is not None
        item.metadata_checked_at=now-timedelta(days=35)
        db.query(Metric).filter_by(video_id=item.id).update({Metric.at:now-timedelta(days=35)})
        db.commit()
        result=cleanup(db,now)
        assert result=={'expired_videos':1,'expired_metric_snapshots':1}
        assert item.views is None and item.original_caption==''
        assert item.source_url=='' and item.remote_video_id=='A'*11


def test_youtube_refresh_replaces_hidden_metrics_with_unknown():
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user=User(telegram_id=555)
        db.add(user);db.flush()
        s=Source(owner_id=user.id,username='youtube:x',platform='youtube',max_age_hours=None)
        db.add(s);db.flush()
        now=datetime.now(timezone.utc)
        v,_=ingest(db,s,4,now,views=1000,reactions=10,replies=5)
        v,_=ingest(db,s,4,now,views=2000,reactions=None,replies=None)
        assert v.views==2000 and v.reactions is None and v.replies is None
        assert v.viral_score is None


def test_csv_formula_injection_protection():
    import ast
    from pathlib import Path
    # Isolate the function source for testing without installing aiogram in offline CI.
    src=Path(__file__).parents[1]/'app'/'telegram_ui_extra.py'
    module=ast.parse(src.read_text(encoding='utf-8'))
    fn=next(x for x in module.body if isinstance(x,ast.FunctionDef) and x.name=='safe_csv_value')
    namespace={}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(src),'exec'),namespace)
    assert namespace['safe_csv_value']('=HYPERLINK("evil")').startswith("'=")
    assert namespace['safe_csv_value']('normal')=='normal'
    assert namespace['safe_csv_value'](None)==''
