from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
from dotenv import dotenv_values
import pytest

MODULE = Path(__file__).resolve().parents[2] / 'scripts' / 'pulse_local.py'
spec = spec_from_file_location('pulse_local', MODULE)
pulse = module_from_spec(spec)
spec.loader.exec_module(pulse)


def test_safe_dotenv_serialization():
    original = 'abc#%=z\\t\" quotes +@/$ and symbols'
    assert dotenv_values(stream=StringIO(pulse.env_line('SECRET', original)))['SECRET'] == original


def test_cloud_db_converts_neon_dialect_only():
    url = 'postgresql://user:pass@ep-test.aws-us-east-1.neon.tech/pulse?sslmode=require'
    assert pulse.validate_host(url) == url.replace('postgresql://', 'postgresql+psycopg://', 1)


@pytest.mark.parametrize('url', [
    'postgresql://user:pass@localhost/pulse?sslmode=require',
    'postgresql://user:pass@ep-test.neon.tech/pulse?sslmode=disable',
    'postgresql://user@ep-test.neon.tech/pulse?sslmode=require',
    'mysql://user:pass@ep-test.neon.tech/pulse?sslmode=require',
])
def test_reject_incorrect_or_unsafe_local_neon_url(url):
    with pytest.raises(ValueError):
        pulse.validate_host(url)
