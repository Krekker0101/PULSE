import pytest
from app.services.cloud_health import validate_cloud_urls

DB = 'postgresql+psycopg://pulse:password@ep-example.us-east-1.aws.neon.tech/pulse?sslmode=require'
RD = 'rediss://:password@optimum-wombat-215901.upstash.io:6379/0?ssl_cert_reqs=required'


def test_neon_and_upstash_urls():
    validate_cloud_urls(DB, RD)


@pytest.mark.parametrize('db,redis', [
    (DB, 'redis://:password@optimum-wombat-215901.upstash.io:6379/0'),
    ('postgresql+psycopg://u:p@localhost:5432/pulse?sslmode=require', RD),
    (DB, 'rediss://:x@redis.example.org:6379/0'),
    (DB.replace('?sslmode=require',''), RD),
    (DB.replace('sslmode=require','sslmode=disable'), RD),
    (DB, RD.replace('ssl_cert_reqs=required','ssl_cert_reqs=none')),
    (DB, 'rediss://optimum-wombat-215901.upstash.io:6379/0'),
])
def test_reject_unsafe_cloud_urls(db,redis):
    with pytest.raises(ValueError): validate_cloud_urls(db,redis)
