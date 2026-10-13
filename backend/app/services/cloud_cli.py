"""Connection diagnostics: PostgreSQL is required, Redis is optional for this release."""
from urllib.parse import urlsplit
from sqlalchemy import create_engine, text
from ..config import get_settings


def run() -> int:
    cfg=get_settings()
    try:
        engine=create_engine(cfg.database_url,pool_pre_ping=True,connect_args={'connect_timeout':8} if cfg.database_url.startswith('postgresql') else {})
        try:
            with engine.connect() as db:db.execute(text('SELECT 1')).scalar_one()
        finally:engine.dispose()
        print('PostgreSQL: OK')
    except Exception as exc:
        print('PostgreSQL: FAILED ('+type(exc).__name__+') — server cannot start')
        return 1
    if not cfg.redis_url or not urlsplit(cfg.redis_url).hostname:
        print('Upstash Redis: not configured (optional; FSM is in PostgreSQL)')
    else:
        try:
            from redis import Redis
            rd=Redis.from_url(cfg.redis_url,socket_connect_timeout=3,socket_timeout=3)
            try:rd.ping()
            finally:rd.close()
            print('Upstash Redis: OK (optional)')
        except Exception as exc:
            print('Upstash Redis: UNAVAILABLE ('+type(exc).__name__+') — not blocking PULSE')
    return 0


if __name__=='__main__':
    raise SystemExit(run())
