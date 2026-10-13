"""Minimal cloud-connection checks. Never include passwords or DSNs in diagnostics."""
from sqlalchemy import create_engine, text
from urllib.parse import urlsplit, parse_qs


def validate_cloud_urls(database_url: str, redis_url: str) -> None:
    # Validate cleartext transport and reject accidental connection to unrelated Redis hosts.
    db = urlsplit(database_url)
    rd = urlsplit(redis_url)
    if db.scheme not in ('postgresql+psycopg', 'postgresql') or not (db.hostname or '').endswith('.neon.tech'):
        raise ValueError('Neon PostgreSQL connection string expected')
    if rd.scheme != 'rediss' or not (rd.hostname or '').endswith('.upstash.io'):
        raise ValueError('Upstash requires encrypted rediss:// connection')
    if rd.port != 6379:
        raise ValueError('Upstash Redis port must be 6379')
    db_ssl = parse_qs(db.query).get('sslmode', [''])[0]
    if db_ssl not in ('require','verify-ca','verify-full'):
        raise ValueError('Neon TLS must use sslmode=require or stronger')
    if parse_qs(rd.query).get('ssl_cert_reqs', [''])[0] != 'required':
        raise ValueError('Redis must verify the TLS certificate')
    if not rd.password:
        raise ValueError('Upstash Redis password is missing')


def cloud_status(database_url: str, redis_url: str) -> dict[str, str]:
    validate_cloud_urls(database_url, redis_url)
    engine = create_engine(database_url, pool_pre_ping=True, connect_args={'connect_timeout': 8})
    try:
        with engine.connect() as con:
            con.execute(text('SELECT 1')).scalar_one()
    finally:
        engine.dispose()
    from redis import Redis
    redis = Redis.from_url(redis_url, socket_connect_timeout=8, socket_timeout=8)
    try:
        if not redis.ping():
            raise RuntimeError('Redis PING failed')
    finally:
        redis.close()
    return {'neon': 'ok', 'upstash': 'ok'}
