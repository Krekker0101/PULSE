"""Optional machine-to-machine endpoints; no dashboard or browser application.

The *only* browser screen is the final OAuth confirmation returned after
an external provider has authenticated the user. Deploy separately and protect
with HTTPS when Instagram/TikTok connections are required.
"""
from pathlib import Path
from typing import Literal
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from .config import get_settings
from .db import get_db
from .models import Source,Video,ClipAsset
from .services.oauth import complete
from .services.signed_media import verify, verify_clip

app=FastAPI(title='PULSE OAuth bridge',docs_url=None,redoc_url=None,openapi_url=None)


@app.get('/healthz')
def healthz(db:Session=Depends(get_db)):
    db.execute(text('SELECT 1'))
    return {'ok':True}


@app.get('/oauth/{provider}/callback')
async def callback(provider:Literal['instagram','tiktok'],code:str='',state:str='',error:str='',db:Session=Depends(get_db)):
    if error or not code or not state:
        return HTMLResponse('<html lang="ru"><meta charset="utf-8"><h2>Авторизация отменена</h2><p>Вернитесь в PULSE в Telegram.</p></html>',status_code=400)
    try:
        await complete(db,provider,state,code)
    except HTTPException as exc:
        return HTMLResponse('<html lang="ru"><meta charset="utf-8"><h2>Подключение не завершено</h2><p>Повторите подключение из PULSE. Проверьте разрешения и тип аккаунта.</p></html>', status_code=exc.status_code,
                            headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
    return HTMLResponse('<html lang="ru"><meta charset="utf-8"><h2>✅ PULSE</h2><p>Аккаунт подключён. Вернитесь в Telegram.</p></html>',
                        headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})


@app.get('/media/{video_id}')
def media(video_id:int,expires:int,signature:str,db:Session=Depends(get_db)):
    if not verify(video_id,expires,signature):raise HTTPException(403,'Invalid/expired signature')
    v=db.get(Video,video_id)
    if not v or not v.media_path:raise HTTPException(404,'Missing media')
    source=db.get(Source,v.source_id)
    if not source or source.rights_status!='user_attested':raise HTTPException(403,'Rights not approved')
    allowed=(get_settings().storage_dir.resolve()/'originals')
    path=Path(v.media_path).resolve()
    if not path.is_relative_to(allowed) or not path.is_file():raise HTTPException(404,'File missing')
    return FileResponse(path,media_type='video/mp4')


@app.get('/clips/{clip_id}')
def clip(clip_id:int,expires:int,signature:str,db:Session=Depends(get_db)):
    if not verify_clip(clip_id,expires,signature):raise HTTPException(403,'Invalid/expired signature')
    v=db.get(ClipAsset,clip_id)
    if not v:raise HTTPException(404,'No clip')
    origin=db.get(Video,v.video_id)
    source=db.get(Source,origin.source_id) if origin else None
    if not source or source.rights_status!='user_attested' or not source.rights_derivative:
        raise HTTPException(403,'Rights not approved')
    allowed=(get_settings().storage_dir.resolve()/'clips')
    path=Path(v.media_path).resolve()
    if not path.is_relative_to(allowed) or not path.is_file():raise HTTPException(404,'File missing')
    return FileResponse(path,media_type='video/mp4')
