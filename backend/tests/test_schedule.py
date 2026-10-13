from datetime import datetime,timezone
from app.domain.schedule import next_slot
import pytest


def test_timezone_and_daily_capacity():
    now=datetime(2026,10,9,6,0,tzinfo=timezone.utc)  # 11:00 Dushanbe
    slot=next_slot(now,'Asia/Dushanbe',list(range(7)),['10:00','15:00'],1,120,[])
    assert slot==datetime(2026,10,9,10,0,tzinfo=timezone.utc)
    reserved=[slot]
    slot2=next_slot(now,'Asia/Dushanbe',list(range(7)),['10:00','15:00'],1,120,reserved)
    assert slot2==datetime(2026,10,10,5,0,tzinfo=timezone.utc)


def test_bad_schedule():
    with pytest.raises(ValueError):next_slot(datetime.now(timezone.utc),'Not/AZone',[0],['10:00'],1,0,[])
    with pytest.raises(ValueError):next_slot(datetime.now(timezone.utc),'UTC',[8],['10:00'],1,0,[])
