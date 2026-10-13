from app.services.clips import suggest
from pathlib import Path
import pytest


def test_reject_missing_video():
    with pytest.raises(FileNotFoundError):suggest(Path('/nonexistent/pulse_file.mp4'))
