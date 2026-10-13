"""FFmpeg only receives argv, never shell strings; no arbitrary external video URLs."""
import hashlib
import json
import os
import subprocess
from pathlib import Path


def _run(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)


def probe(path: Path) -> dict:
    path = Path(path)
    if not path.is_file(): raise FileNotFoundError(path)
    if path.stat().st_size == 0: raise ValueError('Empty media file')
    result = _run(['ffprobe','-v','error','-show_entries','format=duration,size,bit_rate,format_name:stream=codec_type,codec_name,width,height,avg_frame_rate', '-of','json',str(path)], 45)
    data = json.loads(result.stdout)
    videos = [s for s in data.get('streams', []) if s.get('codec_type') == 'video']
    if not videos: raise ValueError('No video stream')
    v = videos[0]
    return {'duration': float(data['format'].get('duration', 0)), 'size': int(data['format'].get('size', 0)),
            'format': data['format'].get('format_name'), 'width': v.get('width'), 'height': v.get('height'),
            'video_codec': v.get('codec_name'), 'fps': v.get('avg_frame_rate'),
            'audio_codec': next((s.get('codec_name') for s in data['streams'] if s.get('codec_type') == 'audio'), None)}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as file:
        for block in iter(lambda: file.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def make_variant(src: Path, dest: Path, mode: str = 'fit', max_seconds: int = 600):
    """Produce 1080x1920 H264/AAC output; trimming requires explicit separate call."""
    if mode not in ('fit', 'crop'): raise ValueError('mode must be fit or crop')
    info = probe(src)
    if info['duration'] > max_seconds: raise ValueError('Source too long for this processing profile')
    dest.parent.mkdir(parents=True, exist_ok=True)
    if mode == 'fit':
        filt = 'scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1'
    else:
        filt = 'scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,setsar=1'
    tmp = dest.with_name(dest.stem + '.processing.mp4')
    try:
        command = ['ffmpeg','-hide_banner','-nostdin','-y','-i',str(src),'-map','0:v:0','-map','0:a:0?', '-vf',filt,
                   '-c:v','libx264','-preset','medium','-crf','20','-pix_fmt','yuv420p', '-c:a','aac','-b:a','160k',
                   '-movflags','+faststart',str(tmp)]
        _run(command, max(120, min(3600, int(info['duration'] * 5))))
        probe(tmp)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return probe(dest)


def clip(src: Path, dest: Path, start: float, end: float):
    info = probe(src)
    if not (0 <= start < end <= info['duration'] + .1 and end-start <= 180):
        raise ValueError('Invalid trim window (maximum 180 seconds)')
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + '.clip-temp.mp4')
    try:
        _run(['ffmpeg','-hide_banner','-nostdin','-y','-ss',str(start),'-i',str(src),'-t',str(end-start),
              '-map','0:v:0','-map','0:a:0?','-c:v','libx264','-preset','fast','-crf','20',
              '-c:a','aac','-movflags','+faststart',str(tmp)], max(120, int((end-start)*5)))
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return probe(dest)
