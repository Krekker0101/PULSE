import shutil
import subprocess
import pytest
from pathlib import Path
from app.services.media import probe, sha256_file, clip

@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),reason='FFmpeg unavailable')
def test_real_ffmpeg_clip(tmp_path:Path):
    src=tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-loglevel','error','-f','lavfi','-i','color=c=black:s=320x240:r=24',
                    '-t','4','-c:v','libx264','-pix_fmt','yuv420p',str(src)],check=True)
    metadata=probe(src)
    assert metadata['video_codec']=='h264'
    assert metadata['duration']>=3.9
    dest=tmp_path/'out.mp4'
    final=clip(src,dest,1,2.5)
    assert 1.4<=final['duration']<=1.6
    assert sha256_file(src)!=sha256_file(dest)
    with pytest.raises(ValueError):clip(src,tmp_path/'bad.mp4',4,2)
