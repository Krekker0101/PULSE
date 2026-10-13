"""DST-conscious user-timezone scheduling. UTC stored; local times display only."""
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re


def validate_schedule(tz:str, days:list, times:list, daily_max:int, gap:int):
    try: ZoneInfo(tz)
    except ZoneInfoNotFoundError: raise ValueError('Unknown IANA timezone') from None
    if not isinstance(days,list) or not days or any(type(d)!=int or d not in range(7) for d in days):
        raise ValueError('Select at least one weekday (0 = Monday)')
    if not isinstance(times,list) or not 1<=len(times)<=24:
        raise ValueError('Specify 1–24 posting times')
    if not all(isinstance(t,str) and re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',t) for t in times):
        raise ValueError('Times must be HH:MM')
    if len(set(times))!=len(times): raise ValueError('Duplicate time slots')
    if not (1<=daily_max<=10 and 0<=gap<=1440):
        raise ValueError('Invalid daily limit or minimum interval')


def next_slot(now:datetime, tz:str,days:list,times:list,daily_max:int,
              min_gap_minutes:int, existing:list[datetime]) -> datetime:
    validate_schedule(tz,days,times,daily_max,min_gap_minutes)
    if now.tzinfo is None: raise ValueError('now must contain timezone')
    zone=ZoneInfo(tz)
    local=now.astimezone(zone)
    existing_utc=[x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x.astimezone(timezone.utc) for x in existing]
    for delta in range(32):
        day=local.date()+timedelta(days=delta)
        if day.weekday() not in days:continue
        per_day=sum(1 for x in existing_utc if x.astimezone(zone).date()==day)
        if per_day>=daily_max:continue
        for hhmm in sorted(times):
            hh,mm=map(int,hhmm.split(':'))
            candidate=datetime(day.year,day.month,day.day,hh,mm,tzinfo=zone)
            # Nonexistent local time during DST spring transition: skip it.
            if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)!=candidate.replace(tzinfo=None):
                continue
            utc=candidate.astimezone(timezone.utc)
            if utc<=now.astimezone(timezone.utc):continue
            if any(abs((utc-other).total_seconds())<min_gap_minutes*60 for other in existing_utc):
                continue
            return utc
    raise ValueError('No schedulable slot within 32 days')
