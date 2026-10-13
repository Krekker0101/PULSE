#!/usr/bin/env python3
"""PULSE local cloud mode, Windows/Linux. No API credentials embedded in distribution."""
from __future__ import annotations
import base64
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from urllib.parse import quote, urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'backend'
FRONTEND = ROOT / 'frontend'
VENV = BACKEND / '.venv'
PYTHON = VENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
PIDFILE = ROOT / '.pulse-runtime.json'
DB_NAME = 'pulse'
REDIS_HOST = 'optimum-wombat-215901.upstash.io'


def env_line(key: str, value: str) -> str:
    # The quoted values are parsed by python-dotenv; json escapes quotes, backslashes, newline.
    return f'{key}={json.dumps(str(value), ensure_ascii=False)}\n'


def secret(prompt: str, default: str = '') -> str:
    value = getpass.getpass(prompt)
    return value if value else default


def env_read() -> dict[str, str]:
    from dotenv import dotenv_values
    return {k:v for k,v in dotenv_values(BACKEND / '.env').items() if v is not None}


def save_env_updates(updates: dict[str, str]):
    """Preserve other keys/comments; replace the file only after all updates succeed."""
    from dotenv import set_key
    target = BACKEND / '.env'
    tmp = target.with_name('.env.oauth.new')
    try:
        tmp.write_text(target.read_text(encoding='utf-8') if target.exists() else '', encoding='utf-8')
        if os.name != 'nt': os.chmod(tmp, 0o600)
        for name, value in updates.items():
            set_key(str(tmp), name, value, quote_mode='always', encoding='utf-8')
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def oauth_setup():
    if not (BACKEND / '.env').is_file():
        raise RuntimeError('Configure PULSE first: backend/.env is missing')
    from types import SimpleNamespace
    from cryptography.fernet import Fernet
    sys.path.insert(0, str(BACKEND))
    from app.services.oauth import configuration_issues
    old = env_read()
    print('Register the provider app and configure public HTTPS before proceeding.')
    print('Instructions: docs/OAUTH_FIRST_SETUP.md. No keys are sent to Telegram.')
    choice = input('Configure 1=Instagram, 2=TikTok, 3=both: ').strip()
    if choice not in ('1', '2', '3'): raise ValueError('Choose 1, 2 or 3')
    providers = ['instagram', 'tiktok'] if choice == '3' else ['instagram' if choice == '1' else 'tiktok']
    url = input('Public HTTPS base URL (Enter keeps existing): ').strip() or old.get('PUBLIC_BASE_URL', '')
    updates = {'PUBLIC_BASE_URL': url.rstrip('/')}
    for provider in providers:
        fields = ('INSTAGRAM_APP_ID', 'INSTAGRAM_APP_SECRET') if provider == 'instagram' else ('TIKTOK_CLIENT_KEY', 'TIKTOK_CLIENT_SECRET')
        for field in fields:
            updates[field] = secret(field + ' (hidden, Enter keeps existing): ', old.get(field, '')).strip()
    merged = {**old, **updates}
    if not merged.get('ENCRYPTION_KEY'):
        updates['ENCRYPTION_KEY'] = Fernet.generate_key().decode()
        merged.update(updates)
    cfg = SimpleNamespace(**{name.lower(): merged.get(name, '') for name in (
        'PUBLIC_BASE_URL', 'ENCRYPTION_KEY', 'INSTAGRAM_APP_ID', 'INSTAGRAM_APP_SECRET', 'TIKTOK_CLIENT_KEY', 'TIKTOK_CLIENT_SECRET')})
    issues = [issue for provider in providers for issue in configuration_issues(cfg, provider)]
    if issues:
        print('\n'.join(dict.fromkeys(issues)))
        raise ValueError('OAuth setup incomplete; existing .env was not changed')
    save_env_updates(updates)
    print('OAuth configuration saved. Database, bot and existing encryption key preserved.')
    for provider in providers:
        print('Register exact redirect URI: ' + updates['PUBLIC_BASE_URL'] + f'/oauth/{provider}/callback')
    print('Restart PULSE. This saves configuration; provider approval and HTTPS availability must be checked separately.')


def exec_checked(argv: list[str], *, cwd: Path) -> None:
    subprocess.run(argv, cwd=str(cwd), check=True)


def ensure_dependencies():
    if sys.version_info < (3,12):
        raise RuntimeError('Python >=3.12 required')
    if not PYTHON.is_file():
        print('Creating Python virtual environment...')
        exec_checked([sys.executable, '-m', 'venv', str(VENV)], cwd=ROOT)
    print('Installing backend Python dependencies...')
    exec_checked([str(PYTHON), '-m', 'pip', 'install', '-r', 'requirements.txt'], cwd=BACKEND)
    if shutil.which('npm'):
        print('Installing frontend dependencies...')
        try:
            exec_checked([shutil.which('npm'), 'install', '--no-audit', '--no-fund'], cwd=FRONTEND)
        except subprocess.CalledProcessError:
            print('Mini App dependencies not installed. API/bot setup can continue. Re-run npm install later.')
    else:
        print('Node.js/npm is not installed. The API and bot can run; Mini App needs Node.js 20+.')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        print('FFmpeg/FFprobe not detected: media conversion and clips require manual FFmpeg installation.')


def validate_host(conn_url: str):
    try:
        parsed = urlsplit(conn_url)
        if parsed.scheme not in ('postgresql','postgresql+psycopg'):
            raise ValueError('Expected postgresql:// connection URL')
        if not (parsed.hostname or '').endswith('.neon.tech'):
            raise ValueError('Expected a Neon .neon.tech hostname')
        if parsed.username is None or parsed.password is None:
            raise ValueError('Neon connection must contain the username and password')
        if parse_qs(parsed.query).get('sslmode', [''])[0] not in ('require','verify-ca','verify-full'):
            raise ValueError('Neon URL needs sslmode=require (or stronger)')
    except ValueError as e:
        raise ValueError(f'Incorrect Neon URL: {e}') from None
    return conn_url.replace('postgresql://','postgresql+psycopg://',1)


def setup():
    print('PULSE — cloud setup (Neon + Upstash). Secrets are entered locally, never printed.')
    ensure_dependencies()
    old = env_read() if (BACKEND/'.env').exists() else {}
    if old:
        reply = input('Existing backend/.env detected. Update it? [y/N]: ').strip().lower()
        if reply not in ('y','yes'): return
    neon = secret('Paste Neon pooled/direct connection URL (hidden, Enter keeps old): ',old.get('DATABASE_URL',''))
    neon = validate_host(neon)
    upstash_password = secret('Upstash Redis password from Console (hidden, Enter keeps old): ')
    if upstash_password:
        redis_url = ('rediss://:'+quote(upstash_password,safe='')+'@'+REDIS_HOST+':6379/0'
                     '?ssl_cert_reqs=required')
    else:
        redis_url = old.get('REDIS_URL','')
    if redis_url and not redis_url.startswith('rediss://'):
        raise ValueError('Upstash URL must use rediss:// TLS')
    print('Redis is optional: Telegram dialog state now persists in PostgreSQL.')
    owner = input('Telegram numeric owner ID (Enter keeps existing): ').strip() or old.get('OWNER_TELEGRAM_ID','0')
    if not owner.isdigit(): raise ValueError('OWNER_TELEGRAM_ID must be numeric')
    bot_token = secret('Telegram bot token from @BotFather (hidden, optional now): ',old.get('BOT_TOKEN',''))
    youtube_key = secret('YouTube Data API v3 key (hidden; Enter keeps existing): ',old.get('YOUTUBE_API_KEY',''))
    key = old.get('ENCRYPTION_KEY') or base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')
    dev_key = old.get('DEVELOPMENT_API_KEY') or secrets.token_urlsafe(32)
    vars = {
        'ENVIRONMENT':'development', 'DATABASE_URL':neon, 'REDIS_URL':redis_url,
        'BOT_TOKEN':bot_token,'YOUTUBE_API_KEY':youtube_key, 'OWNER_TELEGRAM_ID':owner,
        'ENCRYPTION_KEY':key, 'DEVELOPMENT_API_KEY':dev_key,
        'PUBLIC_BASE_URL':old.get('PUBLIC_BASE_URL') or 'http://127.0.0.1:8000', 'MINI_APP_URL':'',
        'STORAGE_DIR':'./storage', 'WORKER_POLL_SECONDS':'10',
        'SOURCE_SCAN_INTERVAL_SECONDS':'900',
    }
    # Retain optional API settings, never replace with defaults silently.
    for optional in ('INSTAGRAM_APP_ID','INSTAGRAM_APP_SECRET','INSTAGRAM_GRAPH_VERSION',
                     'DATABASE_URL_UNPOOLED','ENCRYPTION_PREVIOUS_KEYS','BOT_ONLY','MAX_STORAGE_BYTES','MEDIA_RETENTION_HOURS',
                     'TIKTOK_CLIENT_KEY','TIKTOK_CLIENT_SECRET','TIKTOK_VERIFIED_MEDIA_DOMAIN','TIKTOK_DISCOVERY_ENABLED',
                     'TELEGRAM_API_ID','TELEGRAM_API_HASH','TELEGRAM_SESSION_ENCRYPTED'):
        if optional in old: vars[optional]=old[optional]
    target = BACKEND/'.env'
    data = ''.join(env_line(k,v) for k,v in vars.items())
    tmp = target.with_name('.env.new')
    tmp.write_text(data, encoding='utf-8')
    if os.name != 'nt': os.chmod(tmp,0o600)
    os.replace(tmp,target)
    print('Saved backend/.env (never included in ZIP or Git).')
    if not check():
        print('Connection tests failed. Re-run setup to correct the credentials.')
        return
    print('Running non-destructive Alembic schema upgrade...')
    try:
        exec_checked([str(PYTHON),'-m','alembic','upgrade','head'],cwd=BACKEND)
        print('Database migration completed.')
    except subprocess.CalledProcessError:
        print('Migration unsuccessful; see the error above, do not delete existing data.')
    print('Start with START_PULSE.bat (Windows) or python scripts/pulse_local.py start.')


def check():
    if not (BACKEND/'.env').is_file() or not PYTHON.is_file():
        print('Run SETUP_PULSE.bat first.')
        return False
    # Run check in an isolated subprocess; never print DSNs or stack traces.
    proc = subprocess.run([str(PYTHON),'-m','app.services.cloud_cli'], cwd=str(BACKEND),
                          capture_output=True, text=True, timeout=35)
    print(proc.stdout.strip() or ('Cloud check failed' if proc.returncode else 'Cloud check OK'))
    if proc.returncode:
        print('Check provider console, database password, network firewall and TLS.')
    return proc.returncode == 0


def _processes():
    import psutil
    if not PIDFILE.exists(): return None
    try:
        details=json.loads(PIDFILE.read_text(encoding='utf-8'))
        proc=psutil.Process(details['pid'])
        if abs(proc.create_time()-details['created_at'])>2.0: return None
        if not any('pulse_local.py' in part for part in proc.cmdline()): return None
        return proc
    except (OSError, ValueError, KeyError, psutil.Error):
        return None


def stop():
    import psutil
    proc=_processes()
    if not proc:
        print('No running PULSE supervisor found.')
        return
    targets=proc.children(recursive=True)
    for child in targets:
        try: child.terminate()
        except psutil.Error: pass
    _, alive=psutil.wait_procs(targets, timeout=8)
    for child in alive:
        try: child.kill()
        except psutil.Error: pass
    try: proc.terminate()
    except psutil.Error: pass
    PIDFILE.unlink(missing_ok=True)
    print('PULSE stopped.')


def start():
    import psutil
    if _processes():
        print('PULSE already running. Use STOP_PULSE.bat to stop it.')
        return
    if not check(): return
    # Upgrade before any ORM query. create_all cannot repair existing tables.
    print('Checking and upgrading database schema...')
    exec_checked([str(PYTHON), '-m', 'alembic', 'upgrade', 'head'], cwd=BACKEND)
    children=[]
    me=psutil.Process()
    PIDFILE.write_text(json.dumps({'pid':os.getpid(),'created_at':me.create_time()}),encoding='utf-8')
    if os.name != 'nt': os.chmod(PIDFILE,0o600)
    env=env_read()
    cmds = [([str(PYTHON),'-m','uvicorn','app.api:app','--host','127.0.0.1','--port','8000'],BACKEND,'API'),
            ([str(PYTHON),'-m','app.services.worker'],BACKEND,'worker'),
            ([str(PYTHON),'-m','app.services.source_monitor'],BACKEND,'source-monitor')]
    if env.get('BOT_TOKEN') and env.get('OWNER_TELEGRAM_ID','0')!='0':
        cmds.append(([str(PYTHON),'-m','app.bot'],BACKEND,'bot'))
    else:
        print('Bot not launched: configure BOT_TOKEN and OWNER_TELEGRAM_ID in backend/.env')
    if (FRONTEND/'node_modules').is_dir() and shutil.which('npm'):
        cmds.append(([shutil.which('npm'),'run','dev','--','--host','127.0.0.1'],FRONTEND,'Mini App'))
    try:
        for cmd, cwd, name in cmds:
            children.append((name,subprocess.Popen(cmd,cwd=str(cwd))))
            print(f'Started {name}.')
        print('PULSE API: http://127.0.0.1:8000/health/ready')
        print('Mini App: http://127.0.0.1:5173 (local-only authenticated proxy).')
        print('Press Ctrl+C in this window to stop. PULSE needs this computer running.')
        while True:
            for name,child in children:
                code=child.poll()
                if code is not None:
                    print(f'{name} exited with code {code}. Stopping all processes.')
                    return
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for _,child in reversed(children):
            if child.poll() is None: child.terminate()
        for _,child in reversed(children):
            try: child.wait(timeout=6)
            except subprocess.TimeoutExpired: child.kill()
        PIDFILE.unlink(missing_ok=True)
        print('PULSE processes stopped.')


if __name__=='__main__':
    actions={'setup':setup,'check':check,'start':start,'stop':stop,'oauth-setup':oauth_setup}
    action=sys.argv[1] if len(sys.argv)>1 else ''
    if action not in actions:
        print('Usage: python scripts/pulse_local.py {setup|check|start|stop|oauth-setup}')
        sys.exit(2)
    # Setup runs in system Python; start/stop/check run in venv where psutil is installed.
    if action in ('start','stop','check','oauth-setup') and PYTHON.is_file() and Path(sys.executable).resolve() != PYTHON.resolve():
        raise SystemExit(subprocess.call([str(PYTHON),str(Path(__file__).resolve()),action],cwd=str(ROOT)))
    try: actions[action]()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, EOFError) as exc:
        # Do not include secret-bearing URL in errors.
        print(f'Operation failed ({type(exc).__name__}). Check prerequisites or credentials locally.')
        sys.exit(1)
