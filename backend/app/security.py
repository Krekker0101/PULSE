import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl
from cryptography.fernet import Fernet, MultiFernet
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session
from .config import get_settings
from .db import get_db
from .models import User


def encrypt(value: str) -> str:
    key = get_settings().encryption_key
    if not key:
        raise RuntimeError('ENCRYPTION_KEY is required for social account operations')
    return Fernet(key.encode()).encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    cfg = get_settings()
    keys = [cfg.encryption_key] + [k.strip() for k in cfg.encryption_previous_keys.split(',') if k.strip()]
    return MultiFernet([Fernet(k.encode()) for k in keys]).decrypt(value.encode()).decode()


def verify_telegram_init_data(raw: str, token: str, max_age: int = 3600) -> dict:
    if not token or len(raw) > 8192:
        raise ValueError('Invalid Telegram credentials')
    pairs = dict(parse_qsl(raw, keep_blank_values=True, strict_parsing=True))
    signature = pairs.pop('hash', '')
    pairs.pop('signature', None)  # separate Ed25519 third-party signature is not used
    check = '\n'.join(f'{k}={v}' for k,v in sorted(pairs.items()))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError('Telegram signature mismatch')
    ts = int(pairs.get('auth_date', '0'))
    if not (0 <= time.time()-ts <= max_age):
        raise ValueError('Expired init data')
    user = json.loads(pairs.get('user', '{}'))
    if not isinstance(user.get('id'), int):
        raise ValueError('User ID missing')
    return user


def current_user(x_telegram_init_data: str | None = Header(default=None),
                 x_pulse_dev_key: str | None = Header(default=None),
                 db: Session = Depends(get_db)) -> User:
    settings = get_settings()
    if (settings.environment == 'development' and settings.development_api_key
            and x_pulse_dev_key and hmac.compare_digest(x_pulse_dev_key, settings.development_api_key)):
        tg_id = settings.owner_telegram_id
    else:
        try:
            tg_id = verify_telegram_init_data(x_telegram_init_data or '', settings.bot_token)['id']
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise HTTPException(401, 'Telegram authentication required') from None
    if not tg_id or tg_id != settings.owner_telegram_id:
        raise HTTPException(403, 'Owner-only installation')
    user = db.query(User).filter_by(telegram_id=tg_id).one_or_none()
    if user is None:
        user = User(telegram_id=tg_id)
        db.add(user); db.commit(); db.refresh(user)
    return user
