"""Lyric MV 합치기 (merge): 클립별 구간만 잘라 이어붙이고 원곡 오디오를 통째로 입힘.
사용: python merge_mv.py [원곡.mp3]   (이 파일과 같은 폴더의 plan.json, clip_XX_*.mp4 사용)
"""
import json, os, sys, glob, subprocess, tempfile
here = os.path.dirname(os.path.abspath(__file__))
# 사용: python merge_mv.py [원곡.mp3] [09=clip_09_00005_.mp4 ...]  (클립 번호=파일 로 특정 버전 고정, 없으면 최신)
args = sys.argv[1:]
pins = dict(a.split("=", 1) for a in args if "=" in a)
rest = [a for a in args if "=" not in a]
song = rest[0] if rest else None
if not song:
    sys.exit('usage: python merge_mv.py <song.mp3> [09=clip_09_00002_.mp4 ...]')
plan = json.load(open(os.path.join(here, "plan.json"), encoding="utf-8"))
tmp = tempfile.mkdtemp()
parts, missing = [], []
for c in plan:
    pin = pins.get(f"{c['clip']:02d}")
    files = [os.path.join(here, pin)] if pin else sorted(glob.glob(os.path.join(here, f"clip_{c['clip']:02d}_*.mp4")), key=os.path.getmtime)
    if not files:
        missing.append(c['clip']); continue
    src = files[-1]
    off = round(c["seg_start"] - c["gen_start"], 3)
    dur = round(c["seg_end"] - c["seg_start"], 3)
    out = os.path.join(tmp, f"p{c['clip']:02d}.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(off), "-i", src, "-t", str(dur),
                    "-an", "-vf", "fps=24", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", out], check=True)
    parts.append(out)
    print(f"clip {c['clip']:02d}: {os.path.basename(src)}  offset {off}s  len {dur}s")
if missing:
    print("빠진 클립:", missing); sys.exit(1)
lst = os.path.join(tmp, "list.txt")
open(lst, "w", encoding="utf-8").write("".join(f"file '{p}'\n" for p in parts))
video = os.path.join(tmp, "video.mp4")
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", video], check=True)
final = os.path.join(here, "MV_final.mp4")
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", video, "-i", song, "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", final], check=True)
print("완성:", final)
