import os
import re
import asyncio
import uuid
import wave
import difflib

import numpy as np
import folder_paths
from aiohttp import web
from server import PromptServer

TS_RE = re.compile(r"\[\s*([0-9.]+)\s*-\s*([0-9.]+)\s*\]\s*(.*)")
TAG_RE = re.compile(r"^\s*\[[^\]]*\]\s*")
NORM_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def _norm(s):
    return NORM_RE.sub("", s).lower()


def parse_word_timestamps(text):
    """Parse Qwen3-ASR timestamps ("[s - e] word" per line) and drop chunk-overlap duplicates."""
    words = []
    for line in (text or "").splitlines():
        m = TS_RE.match(line.strip())
        if not m:
            continue
        s, e, w = float(m.group(1)), float(m.group(2)), m.group(3).strip()
        if not w:
            continue
        words.append((s, max(e, s), w))
    words.sort(key=lambda x: x[0])
    out = []
    for s, e, w in words:
        if out and s < out[-1][1] - 0.05:
            # overlapping chunk region: keep the earlier copy
            if _norm(w) == _norm(out[-1][2]) or s < out[-1][0] + 0.05:
                continue
            if any(_norm(w) == _norm(p[2]) and abs(s - p[0]) < 1.0 for p in out[-6:]):
                continue
        out.append((s, e, w))
    return out


def parse_lyrics(text):
    lines = []
    for raw in (text or "").splitlines():
        line = TAG_RE.sub("", raw).strip()
        while TAG_RE.match(line):
            line = TAG_RE.sub("", line).strip()
        if line and _norm(line):
            lines.append(line)
    return lines


def fmt_time(t):
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cues_to_srt(cues):
    blocks = []
    for i, (s, e, t) in enumerate(cues, 1):
        blocks.append(f"{i}\n{fmt_time(s)} --> {fmt_time(e)}\n{t}\n")
    return "\n".join(blocks)


def group_words(words, max_gap, max_chars, max_sec):
    cues, cur = [], []
    for w in words:
        if cur:
            gap = w[0] - cur[-1][1]
            text_len = len("".join(x[2] for x in cur)) + len(w[2])
            if gap > max_gap or text_len > max_chars or w[1] - cur[0][0] > max_sec:
                cues.append(cur)
                cur = []
        cur.append(w)
    if cur:
        cues.append(cur)
    return [(c[0][0], c[-1][1], " ".join(x[2] for x in c)) for c in cues]


def align_lyrics(words, lines):
    """Map known lyric lines onto ASR word timings via a global character alignment."""
    asr_chars, asr_times = [], []
    for s, e, w in words:
        n = _norm(w)
        if not n:
            continue
        step = (e - s) / len(n)
        for k, ch in enumerate(n):
            asr_chars.append(ch)
            asr_times.append((s + k * step, s + (k + 1) * step))
    lyr_chars, owner = [], []
    for li, line in enumerate(lines):
        for ch in _norm(line):
            lyr_chars.append(ch)
            owner.append(li)
    sm = difflib.SequenceMatcher(None, lyr_chars, asr_chars, autojunk=False)
    span = [[None, None] for _ in lines]
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            li = owner[a + k]
            ts, te = asr_times[b + k]
            if span[li][0] is None or ts < span[li][0]:
                span[li][0] = ts
            if span[li][1] is None or te > span[li][1]:
                span[li][1] = te
    cues, unmatched = [], 0
    for li, line in enumerate(lines):
        s, e = span[li]
        if s is None:
            unmatched += 1
            prev_end = cues[-1][1] if cues else 0.0
            s, e = prev_end + 0.1, prev_end + 2.0
        cues.append([s, e, line])
    for i in range(1, len(cues)):  # keep order / no overlap
        if cues[i][0] < cues[i - 1][1]:
            cues[i][0] = cues[i - 1][1] + 0.01
            cues[i][1] = max(cues[i][1], cues[i][0] + 0.3)
    return [tuple(c) for c in cues], unmatched


class LyricTimestampsToSRT:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "timestamps": ("STRING", {"forceInput": True, "tooltip": "Qwen3-ASR Transcriber 'timestamps' output (needs the Forced Aligner)."}),
                "lyrics": ("STRING", {"multiline": True, "default": "", "tooltip": "Optional. Known lyrics, one subtitle per line ([verse] tags ignored). Empty = auto-split from ASR words."}),
                "max_gap": ("FLOAT", {"default": 0.7, "min": 0.1, "max": 5.0, "step": 0.05, "tooltip": "Auto mode: silence (s) that starts a new subtitle."}),
                "max_chars": ("INT", {"default": 24, "min": 4, "max": 200, "tooltip": "Auto mode: max characters per subtitle."}),
                "max_seconds": ("FLOAT", {"default": 6.0, "min": 1.0, "max": 30.0, "step": 0.5, "tooltip": "Auto mode: max subtitle length (s)."}),
                "end_pad": ("FLOAT", {"default": 0.15, "min": 0.0, "max": 2.0, "step": 0.05, "tooltip": "Seconds added after each line's last word (clipped to the next line)."}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("srt",)
    FUNCTION = "convert"
    CATEGORY = "LyricSRT"

    def convert(self, timestamps, lyrics, max_gap, max_chars, max_seconds, end_pad):
        words = parse_word_timestamps(timestamps)
        if not words:
            raise ValueError("LyricSRT: no word timestamps found. Connect a Qwen3 Forced Aligner Config to the transcriber.")
        lines = parse_lyrics(lyrics)
        if lines:
            cues, unmatched = align_lyrics(words, lines)
            if unmatched:
                print(f"[LyricSRT] {unmatched} lyric line(s) had no ASR match; placed after the previous line - fix them in the editor.")
        else:
            cues = group_words(words, max_gap, max_chars, max_seconds)
        padded = []
        for i, (s, e, t) in enumerate(cues):
            nxt = cues[i + 1][0] if i + 1 < len(cues) else e + end_pad
            padded.append((s, min(e + end_pad, max(e, nxt - 0.01)), t))
        return (cues_to_srt(padded),)


def _write_wav(path, waveform, sample_rate):
    wf = waveform
    if hasattr(wf, "cpu"):
        wf = wf.cpu().float().numpy()
    wf = np.asarray(wf)
    if wf.ndim == 3:
        wf = wf[0]
    if wf.ndim == 1:
        wf = wf[None]
    pcm = (np.clip(wf.T, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm.tobytes())


DEFAULT_DIR = "lyric_srt"


def _safe_name(name):
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", (name or "").strip()).replace("..", "_")
    name = name.strip("/\\ ")
    if name.lower().endswith(".srt"):
        name = name[:-4]
    return name


def _upstream_audio_name(prompt, unique_id):
    """Follow the 'audio' link back to a LoadAudio-style node and return its file stem."""
    try:
        node = prompt[str(unique_id)]
        seen = set()
        link = node["inputs"].get("audio")
        while isinstance(link, list) and link and str(link[0]) not in seen:
            nid = str(link[0])
            seen.add(nid)
            up = prompt.get(nid, {})
            inputs = up.get("inputs", {})
            val = inputs.get("audio")
            if isinstance(val, str) and val:
                return os.path.splitext(os.path.basename(val))[0]
            link = val if isinstance(val, list) else next((v for v in inputs.values() if isinstance(v, list)), None)
    except Exception:
        pass
    return ""


def _resolve_dir(save_dir):
    save_dir = (save_dir or "").strip().strip('"')
    out_dir = os.path.abspath(folder_paths.get_output_directory())
    if not save_dir:
        return os.path.join(out_dir, DEFAULT_DIR)
    if os.path.isabs(save_dir):
        return os.path.abspath(save_dir)
    path = os.path.abspath(os.path.join(out_dir, save_dir))
    return path if path.startswith(out_dir) else os.path.join(out_dir, DEFAULT_DIR)


def _write_srt(path, text):
    if not path.lower().endswith(".srt"):
        raise ValueError("only .srt files can be written")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class LyricSRTEditor:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO",),
                "srt": ("STRING", {"forceInput": True}),
                "filename": ("STRING", {"default": "", "tooltip": "Empty = auto (the loaded audio file's name). Saved as <filename>.srt, auto version <filename>_auto.srt."}),
                "save_dir": ("STRING", {"default": "", "tooltip": "Empty = output/lyric_srt. A relative path goes under output/, an absolute path (e.g. D:\\subs) is used as-is. Or pick one with the editor's '다른 위치에 저장' button."}),
            },
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("srt", "srt_path")
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "LyricSRT"

    def run(self, audio, srt, filename, save_dir, prompt=None, unique_id=None):
        name = _safe_name(filename) or _safe_name(_upstream_audio_name(prompt or {}, unique_id)) or "lyrics"
        folder = _resolve_dir(save_dir)
        final_path = os.path.join(folder, name + ".srt")
        _write_srt(os.path.join(folder, name + "_auto.srt"), srt)
        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)
        wav_name = f"lyricsrt_{uuid.uuid4().hex[:10]}.wav"
        _write_wav(os.path.join(temp_dir, wav_name), audio["waveform"], audio["sample_rate"])
        return {"ui": {"lyric_srt": [{"srt": srt, "audio": wav_name, "path": final_path}]}, "result": (srt, final_path)}


def _tk_save_dialog(initial_path):
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.asksaveasfilename(
            parent=root,
            title="SRT 저장 위치 선택",
            initialdir=os.path.dirname(initial_path) if initial_path else None,
            initialfile=os.path.basename(initial_path) if initial_path else "lyrics.srt",
            defaultextension=".srt",
            filetypes=[("SubRip subtitles", "*.srt")],
        ) or ""
    finally:
        root.destroy()


@PromptServer.instance.routes.post("/lyric_srt/save")
async def lyric_srt_save(request):
    data = await request.json()
    path = os.path.abspath(str(data.get("path") or ""))
    try:
        _write_srt(path, data.get("srt") or "")
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    return web.json_response({"ok": True, "path": path})


@PromptServer.instance.routes.post("/lyric_srt/pick_save")
async def lyric_srt_pick_save(request):
    data = await request.json()
    loop = asyncio.get_running_loop()
    try:
        path = await loop.run_in_executor(None, _tk_save_dialog, str(data.get("path") or ""))
    except Exception as e:
        return web.json_response({"ok": False, "error": f"dialog failed: {e}"}, status=500)
    return web.json_response({"ok": bool(path), "path": os.path.abspath(path) if path else ""})


@PromptServer.instance.routes.post("/lyric_srt/reveal")
async def lyric_srt_reveal(request):
    data = await request.json()
    folder = os.path.dirname(os.path.abspath(str(data.get("path") or "")))
    if not os.path.isdir(folder):
        return web.json_response({"ok": False, "error": "folder not found"}, status=404)
    if hasattr(os, "startfile"):
        os.startfile(folder)
    return web.json_response({"ok": True, "path": folder})


NODE_CLASS_MAPPINGS = {
    "LyricTimestampsToSRT": LyricTimestampsToSRT,
    "LyricSRTEditor": LyricSRTEditor,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "LyricTimestampsToSRT": "Lyric Timestamps → SRT",
    "LyricSRTEditor": "Lyric SRT Editor (재생+보정+저장)",
}
