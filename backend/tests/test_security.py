import hashlib
import hmac
import json
import time
import pytest
from urllib.parse import urlencode
from app.security import verify_telegram_init_data
from app.services.catalog import normalize_source


def test_telegram_init_data_validates_signature_and_expiration():
    token='12345:demo-token'
    params={'auth_date':str(int(time.time())),'user':json.dumps({'id':12345},separators=(',',':')),'query_id':'abc'}
    check='\n'.join(f'{k}={v}' for k,v in sorted(params.items()))
    key=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
    params['hash']=hmac.new(key,check.encode(),hashlib.sha256).hexdigest()
    signed=urlencode(params)
    assert verify_telegram_init_data(signed,token)['id']==12345
    with pytest.raises(ValueError):verify_telegram_init_data(signed+'bad',token)
    params['auth_date']='1'
    with pytest.raises(ValueError):verify_telegram_init_data(urlencode(params),token)


def test_source_normalization_rejects_phishing_urls():
    assert normalize_source('https://t.me/Great_Channel')=='great_channel'
    assert normalize_source('@great_channel')=='great_channel'
    with pytest.raises(ValueError):normalize_source('https://evil.invalid/@great_channel')
    with pytest.raises(ValueError):normalize_source('https://t.me/+privateinvite')
