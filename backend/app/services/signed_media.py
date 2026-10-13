import hashlib
import hmac
import time
from urllib.parse import urlencode
from ..config import get_settings


def _sign(video_id: int, expires: int) -> str:
    secret = get_settings().encryption_key
    if not secret: raise ValueError('Media signing key missing')
    return hmac.new(secret.encode(), f'{video_id}:{expires}'.encode(), hashlib.sha256).hexdigest()


def public_url(video_id: int, ttl: int = 4*3600) -> str:
    expires = int(time.time()) + ttl
    return f'{get_settings().public_base_url.rstrip("/")}/media/{video_id}?' + urlencode({'expires':expires,'signature':_sign(video_id, expires)})


def verify(video_id: int, expires: int, signature: str) -> bool:
    return int(time.time()) <= expires <= int(time.time()) + 24*3600 and hmac.compare_digest(_sign(video_id, expires), signature)


def clip_signature(clip_id:int,expires:int) -> str:
    return hmac.new(get_settings().encryption_key.encode(),f'clip:{clip_id}:{expires}'.encode(),hashlib.sha256).hexdigest()


def public_clip_url(clip_id:int,ttl:int=3600) -> str:
    expires=int(time.time())+ttl
    return f'{get_settings().public_base_url.rstrip("/")}/clips/{clip_id}?' + urlencode({'expires':expires,'signature':clip_signature(clip_id,expires)})


def verify_clip(clip_id:int,expires:int,signature:str) -> bool:
    return int(time.time())<=expires<=int(time.time())+86400 and hmac.compare_digest(clip_signature(clip_id,expires),signature)
