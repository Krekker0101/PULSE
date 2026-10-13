from pathlib import Path
import httpx
from ..config import get_settings
from ..services.media import sha256_file


async def download_bot_file(file_id: str, video_id: int):
    settings = get_settings()
    if not settings.bot_token: raise RuntimeError('BOT_TOKEN required')
    api = f'https://api.telegram.org/bot{settings.bot_token}'
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        r = await client.get(f'{api}/getFile', params={'file_id': file_id}); r.raise_for_status()
        data = r.json()
        if not data.get('ok'): raise RuntimeError('Telegram getFile failed')
        remote = data['result']
        if remote.get('file_size', 0) > settings.max_file_bytes:
            raise ValueError('File exceeds quota')
        file_path = remote['file_path']
        # Telegram Bot API returns a remote path. It must never come from user input.
        if file_path.startswith('/') or '..' in file_path.split('/'):
            raise ValueError('Unexpected Telegram file path')
        url = f'https://api.telegram.org/file/bot{settings.bot_token}/{file_path}'
        base = settings.storage_dir.resolve() / 'originals'
        base.mkdir(parents=True, exist_ok=True)
        used = sum(p.stat().st_size for p in settings.storage_dir.resolve().rglob('*') if p.is_file())
        if used + remote.get('file_size', 0) > settings.max_storage_bytes:
            raise ValueError('Storage quota exhausted; wait for media cleanup')
        target = base / f'{video_id}.mp4'
        tmp = base / f'{video_id}.part'
        count = 0
        try:
            async with client.stream('GET', url, timeout=120) as stream:
                stream.raise_for_status()
                with tmp.open('wb') as out:
                    async for chunk in stream.aiter_bytes(262144):
                        count += len(chunk)
                        if count > settings.max_file_bytes or used + count > settings.max_storage_bytes:
                            raise ValueError('File exceeds storage quota')
                        out.write(chunk)
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)
    return str(target), sha256_file(target)
