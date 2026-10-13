"""Package source distribution without credentials, databases, media or tooling caches."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
archive = root / 'PULSE-social-publishing.zip'
excluded = {'.venv', '.git', '.aws', '.codex', '.agents', '__pycache__', '.pytest_cache', '.ruff_cache', 'storage', 'backups'}
allowed = {'.py', '.md', '.txt', '.ini', '.yml', '.yaml'}
with ZipFile(archive, 'w', ZIP_DEFLATED) as out:
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in excluded for part in relative.parts): continue
        if path.name.startswith('.env') and not path.name.endswith('.example'): continue
        if path.suffix not in allowed and path.name not in ('.gitignore', '.dockerignore', '.env.telegram.example', 'Dockerfile.backend'): continue
        out.write(path, relative.as_posix())
print(archive)
