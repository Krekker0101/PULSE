"""Heuristic scene-boundary extraction; not speech-aware and not presented as AI."""
import re
import subprocess
from pathlib import Path
from .media import probe


def suggest(path:Path,max_clips:int=3,clip_length:int=40) -> list[dict]:
    duration=probe(path)['duration']
    if duration < 15:return []
    if duration > 3600:raise ValueError('Suggest clips for at most one hour of video')
    # Sample 2 fps and detect scene changes. Constrain processing to avoid unbounded jobs.
    command=['ffmpeg','-hide_banner','-nostdin','-i',str(path),'-vf',
             "fps=2,select='gt(scene,0.28)',showinfo",'-an','-f','null','-']
    try:
        result=subprocess.run(command,capture_output=True,text=True,timeout=min(900,max(90,int(duration*1.2))))
        found=sorted({float(x) for x in re.findall(r'pts_time:([0-9]+(?:\.[0-9]+)?)',result.stderr)})
    except subprocess.TimeoutExpired:found=[]
    candidates=[x for x in found if 4<x<duration-12]
    if not candidates:candidates=[duration*f for f in (.2,.5,.8)]
    clips=[]
    for candidate in candidates:
        start=max(0,min(duration-12,candidate))
        end=min(duration,start+clip_length)
        if any(abs(start-x['start'])<clip_length for x in clips):continue
        clips.append({'start':round(start,1),'end':round(end,1),
                      'reason':'Смена сцены' if found else 'Равномерный временной интервал'})
        if len(clips)>=max_clips:break
    return clips
