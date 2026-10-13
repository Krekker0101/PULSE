import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DATABASE_URL','sqlite:///:memory:')
os.environ.setdefault('ENVIRONMENT','development')
