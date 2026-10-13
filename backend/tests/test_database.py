from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db import Base
from app.models import User,Source,Video,SocialAccount,Route,Job,Metric
from app.services.catalog import ingest


def test_ingest_deduplicates_and_creates_jobs_only_if_rights():
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user=User(telegram_id=991);db.add(user);db.flush()
        src=Source(owner_id=user.id,username='examplechannel',format_mode='SHORT',min_views=1000,
                   rights_status='user_attested',rights_evidence='I own these videos',rights_instagram=True)
        acc=SocialAccount(owner_id=user.id,provider='instagram',remote_id='test',
                          username='ig',encrypted_access_token='encrypted')
        db.add_all([src,acc]);db.flush()
        db.add(Route(owner_id=user.id,source_id=src.id,account_id=acc.id,mode='APPROVAL'))
        db.commit()
        a,created=ingest(db,src,1,datetime.now(timezone.utc),duration=30,views=999)
        assert created and not a.eligible and db.query(Job).count()==0
        b,created=ingest(db,src,1,datetime.now(timezone.utc),duration=30,views=1100)
        assert not created and b.eligible and a.id==b.id
        assert db.query(Job).count()==1 and db.query(Metric).count()==2
        ingest(db,src,1,datetime.now(timezone.utc),duration=30,views=1100)
        assert db.query(Job).count()==1 and db.query(Metric).count()==2
