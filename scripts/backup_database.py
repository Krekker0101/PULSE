"""Run on the remote host with PostgreSQL client installed; no secrets in argv."""
import argparse
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.engine import make_url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', default='./backups')
    args = parser.parse_args()
    raw = os.environ.get('DATABASE_URL_UNPOOLED') or os.environ.get('DATABASE_URL', '')
    url = make_url(raw)
    if not url.drivername.startswith('postgresql') or '-pooler' in (url.host or ''):
        raise SystemExit('A direct PostgreSQL connection is required for backups.')
    env = {**os.environ, 'PGHOST': url.host or '', 'PGPORT': str(url.port or 5432),
           'PGUSER': url.username or '', 'PGPASSWORD': url.password or '',
           'PGDATABASE': url.database or '', 'PGSSLMODE': url.query.get('sslmode', 'require')}
    root = Path(args.directory).resolve(); root.mkdir(parents=True, exist_ok=True)
    target = root / ('pulse-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '.dump')
    try:
        subprocess.run(['pg_dump', '--format=custom', '--file', str(target)], env=env, check=True)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    print(target)


if __name__ == '__main__': main()
