"""SRT -> music-video clip plan. Groups subtitle lines into clips that cut inside the
silences between lines, adds instrumental clips for long gaps, and hands each clip's
timing + lyric prompt to a per-clip picker node."""
import json
import math
import os
import re

import folder_paths

TIME_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")


def _t(s):
    m = TIME_RE.search(s)
    if not m:
        raise ValueError(f"bad SRT time: {s!r}")
    h, mi, se, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(se) + int(ms.ljust(3, "0")[:3]) / 1000.0


HUM_RE = re.compile(r"^[\[(]?\s*(허밍|hum|humming|음+|흠+|m+)\s*[~.…\-]*\s*[\])]?$", re.IGNORECASE)


def parse_srt(text, with_hums=False):
    """Lyric cues (start, end, text). A cue whose text is a hum marker such as
    [허밍], (hum), 음~, mmm is returned separately as a hum event; other [..] markers
    like [간주] are ignored."""
    cues, hums = [], []
    for block in re.split(r"\n\s*\n", (text or "").replace("\r", "").strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            continue
        a, b = lines[ti].split("-->")
        txt = " ".join(lines[ti + 1:]).strip()
        if not txt:
            continue
        if HUM_RE.match(txt):
            hums.append((_t(a), _t(b)))
        elif not re.fullmatch(r"\[.*\]", txt):
            cues.append((_t(a), _t(b), txt))
    cues.sort(key=lambda c: c[0])
    hums.sort()
    return (cues, hums) if with_hums else cues


def _fmt_rel(t):
    t = max(0.0, t)
    return f"{int(t // 60):02d}:{t - 60 * int(t // 60):06.3f}"


def _split(start, end, max_len):
    n = max(1, math.ceil((end - start) / max_len - 1e-6))
    step = (end - start) / n
    return [(start + i * step, start + (i + 1) * step) for i in range(n)]


def build_plan(cues, song_end, max_lines, max_clip, inst_gap, handle, margin=0.3):
    """Time-based packing: fill each clip with as many whole lines as fit inside
    max_clip (minus the render handles); a line that would cross the limit moves to the
    next clip. Cuts land inside the silence between lines. Silences >= inst_gap become
    their own instrumental clips."""
    if not cues:
        raise ValueError("SRT has no lyric lines")
    song_end = max(song_end, cues[-1][1] + 0.5)
    limit = max_clip - 2 * handle
    segs = []  # [start, end, lines]

    def add_instrumental(a, b):
        if b - a > 0.3:
            for s, e in _split(a, b, limit):
                segs.append([s, e, []])

    pos, i, n = 0.0, 0, len(cues)
    if cues[0][0] - margin >= inst_gap:
        add_instrumental(0.0, cues[0][0] - margin)
        pos = cues[0][0] - margin
    while i < n:
        start = pos
        j = i
        while j < n and cues[j][1] + margin - start <= limit and (max_lines <= 0 or j - i < max_lines):
            j += 1
        if j == i:
            j = i + 1  # one line longer than the limit: it gets its own clip, trimmed to the limit
            print(f"[LyricSRT] line '{cues[i][2]}' is longer than {limit:.1f}s; its clip is capped.")
        last_end = cues[j - 1][1]
        if j < n:
            nxt = cues[j][0]
            if nxt - last_end >= inst_gap:
                cut = min(last_end + margin, start + limit)
                segs.append([start, cut, cues[i:j]])
                add_instrumental(cut, nxt - margin)
                pos = max(cut, nxt - margin)
            else:
                cut = min((last_end + nxt) / 2.0, start + limit)
                segs.append([start, max(cut, last_end), cues[i:j]])
                pos = max(cut, last_end)
        else:
            if song_end - last_end >= inst_gap or song_end - start > limit:
                cut = min(last_end + margin, start + limit)
                segs.append([start, cut, cues[i:j]])
                add_instrumental(cut, song_end)
            else:
                segs.append([start, song_end, cues[i:j]])
        i = j

    plan = []
    for i, (s, e, g) in enumerate(segs, 1):
        gs = max(0.0, s - handle)
        ge = min(song_end, e + handle)
        plan.append({
            "clip": i,
            "title": ("가사 " if g else "간주 ") + f"{s:.1f}-{e:.1f}",
            "seg_start": round(s, 3),
            "seg_end": round(e, 3),
            "gen_start": round(gs, 3),
            "gen_len": round(min(ge - gs, 15.0), 3),
            "lyrics": [c[2] for c in g],
        })
    return plan


class LyricSRTClipPlan:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "srt_path": ("STRING", {"default": "", "tooltip": "SRT file (absolute path, or relative to output/). Usually the file you saved from the Lyric SRT Editor."}),
                "max_lines_per_clip": ("INT", {"default": 0, "min": 0, "max": 20, "tooltip": "0 = no limit: clips are packed by time only. Set a number to also cap how many lines one clip holds."}),
                "max_clip_sec": ("FLOAT", {"default": 15.0, "min": 4.0, "max": 15.0, "step": 0.1, "tooltip": "Longest clip rendered, handles included (MiniMax H3 / FastH3: 15 s). Lines are packed until the next one would cross this; that line moves to the next clip."}),
                "instrumental_gap_sec": ("FLOAT", {"default": 6.0, "min": 0.5, "max": 30.0, "step": 0.1, "tooltip": "A silence between lines at least this long becomes its own instrumental clip (no lip-sync)."}),
                "handle_sec": ("FLOAT", {"default": 0.25, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip": "Extra video rendered before/after each clip, trimmed away when merging."}),
                "language": ("STRING", {"default": "Korean", "tooltip": "Language tag used inside <d>[Language] ...</d>."}),
                "output_folder": ("STRING", {"default": "mv_clips", "tooltip": "Folder under output/ for the clips and plan.json (read by merge_mv.py)."}),
            },
            "optional": {
                "audio": ("AUDIO", {"tooltip": "The song, so the last clip ends exactly at the song end."}),
            },
        }

    RETURN_TYPES = ("STRING", "INT", "STRING")
    RETURN_NAMES = ("plan", "clip_count", "summary")
    FUNCTION = "run"
    CATEGORY = "LyricSRT"

    def run(self, srt_path, max_lines_per_clip, max_clip_sec, instrumental_gap_sec, handle_sec, language, output_folder, audio=None):
        p = (srt_path or "").strip().strip('"')
        if not p:
            raise ValueError("LyricSRT Clip Plan: set srt_path")
        if not os.path.isabs(p):
            p = os.path.join(folder_paths.get_output_directory(), p)
        with open(p, encoding="utf-8-sig") as f:
            cues, hums = parse_srt(f.read(), with_hums=True)
        song_end = cues[-1][1] + 2.0 if cues else 0.0
        if audio is not None:
            wf = audio["waveform"]
            song_end = wf.shape[-1] / float(audio["sample_rate"])
        plan = build_plan(cues, song_end, max_lines_per_clip, max_clip_sec, instrumental_gap_sec, handle_sec)
        for c in plan:  # hum events, as times relative to the rendered clip
            c["hums"] = [[round(max(hs, c["seg_start"]) - c["gen_start"], 3), round(min(he, c["seg_end"]) - c["gen_start"], 3)]
                         for hs, he in hums if he > c["seg_start"] and hs < c["seg_end"]]
        folder = re.sub(r'[<>:"|?*]', "_", (output_folder or "mv_clips").strip().strip("/\\")) or "mv_clips"
        for c in plan:
            c["prefix"] = f"{folder}/clip_{c['clip']:02d}"
        out_dir = os.path.join(folder_paths.get_output_directory(), folder)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "plan.json"), "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=1)
        rows = [f"{c['clip']:02d}  {c['seg_start']:6.2f} ~ {c['seg_end']:6.2f}  ({c['seg_end'] - c['seg_start']:4.1f}s)  "
                + (" / ".join(c["lyrics"]) if c["lyrics"] else "[간주]")
                + ("".join(f"  [허밍 +{h[0]:.1f}s]" for h in c["hums"])) for c in plan]
        summary = f"{len(plan)} clips from {len(cues)} lines  ->  {out_dir}\\plan.json\n" + "\n".join(rows)
        print("[LyricSRT] " + summary)
        return (json.dumps({"clips": plan, "language": language, "folder": folder}, ensure_ascii=False), len(plan), summary)


class LyricSRTClipPick:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "plan": ("STRING", {"forceInput": True, "tooltip": "plan output of SRT -> Clip Plan"}),
                "clip_index": ("INT", {"default": 1, "min": 1, "max": 999, "tooltip": "Which clip of the plan (1 = first)."}),
                "camera": ("STRING", {"multiline": True, "default": "A medium close-up matches <Picture 1>; the camera slowly pushes in.", "tooltip": "Shot / camera description for this clip."}),
                "action": ("STRING", {"multiline": True, "default": "She sings with gentle emotion, one hand on her headphones.", "tooltip": "What she does around the lyrics (sung clips) or during the instrumental."}),
            }
        }

    RETURN_TYPES = ("FLOAT", "FLOAT", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("start_sec", "length_sec", "prompt", "filename_prefix", "lyrics")
    FUNCTION = "pick"
    CATEGORY = "LyricSRT"

    def pick(self, plan, clip_index, camera, action):
        plan = json.loads(plan) if isinstance(plan, str) else plan
        clips = plan["clips"]
        if clip_index > len(clips):
            raise ValueError(f"clip_index {clip_index} > plan has {len(clips)} clips - mute this clip node")
        c = clips[clip_index - 1]
        lang = plan.get("language") or "Korean"
        camera, action = (camera or "").strip(), (action or "").strip()
        # Everything after subject_definitions is generated here, so sung and
        # instrumental clips never share wording: an instrumental clip must not
        # describe a singing voice anywhere, or the lip-sync model opens her mouth.
        retention = ("retention_analysis:\n"
                     "<Subject 1> (appears in [Shot 1]): fully_preserved - her face, hair, clothing, headphones, the microphone and the studio are retained.\n"
                     "<Audio 1>: fully_copy - <Audio 1> is reused 1:1 as the target video's complete final audio track.\n\n")
        look = ("detailed_description:\n"
                "The target video is a cinematic, warmly lit studio scene with soft shallow depth of field. One continuous shot, no cuts.\n")
        hums = c.get("hums") or []
        hum_txt = "".join(f" At {_fmt_rel(s)} she closes her eyes and hums a soft, low 'mmm' with her lips closed, absorbed in the music with a serious, relaxed face, her throat and cheeks moving slightly, until {_fmt_rel(e)}." for s, e in hums)
        if c["lyrics"]:
            sung = " ".join(l.rstrip(".") + "." for l in c["lyrics"])
            prompt = (f"<Audio 1> is the complete music track for <Subject 1> (S1): her soft, warm {lang} female singing voice over gentle acoustic pop.\n\n"
                      "summary:\n[reference generation + audio reuse] A single-shot recording-studio music video moment. "
                      "<Subject 1> sings into the microphone, her lips synced exactly to the vocal in <Audio 1>.\n\n"
                      + retention + look +
                      f"[Shot 1] {camera} <Subject 1> (S1) sings into the microphone, <d>[{lang}] {sung}</d>{hum_txt} {action}\n\n"
                      "overall_soundscape:\nHer sung vocal over soft acoustic guitar and light piano in a quiet treated studio room.\n\n"
                      "non_diegetic_music:\nN/A")
        elif hums:
            # explicit timeline: silent listening until the hum, then the hum as a <d> line
            hs = _fmt_rel(round(hums[0][0], 1))
            he = _fmt_rel(round(hums[-1][1], 1))
            prompt = (f"<Audio 1> is an instrumental passage of gentle acoustic pop on guitar and piano; at {hs} <Subject 1> (S1) begins a soft closed-mouth hum that lasts until {he}.\n\n"
                      "summary:\n[reference generation + audio reuse] A quiet single-shot recording-studio moment during an instrumental break. "
                      f"<Subject 1> listens silently with her lips closed, then at {hs} starts humming along softly with her lips closed, synced to the hum in <Audio 1>.\n\n"
                      + retention + look +
                      f"[Shot 1] {camera} From 00:00.000 to {hs}, <Subject 1> (S1) listens silently with her lips closed and a relaxed, absorbed expression. {action} "
                      f"At {hs}, she closes her eyes and starts humming, <d>[{lang}] 음~</d>, a soft low hum with her lips closed and her throat moving gently, continuing until {he}.\n\n"
                      f"overall_soundscape:\nSoft acoustic guitar and light piano in a quiet treated studio room; from {hs} her soft closed-mouth hum.\n\n"
                      "non_diegetic_music:\nN/A")
        else:
            prompt = ("<Audio 1> is an instrumental passage of gentle acoustic pop played on guitar and piano only.\n\n"
                      "summary:\n[reference generation + audio reuse] A quiet single-shot recording-studio moment during an instrumental break. "
                      "<Subject 1> listens to the music with her lips gently closed the whole time, swaying to the rhythm.\n\n"
                      + retention + look +
                      f"[Shot 1] {camera} <Subject 1> (S1) keeps her lips softly closed in a calm smile the whole time; she only listens to the instrumental music. {action}\n\n"
                      "overall_soundscape:\nOnly soft acoustic guitar and light piano in a quiet treated studio room.\n\n"
                      "non_diegetic_music:\nN/A")
        return (float(c["gen_start"]), float(c["gen_len"]), prompt.strip(), c["prefix"], " / ".join(c["lyrics"]))


NODE_CLASS_MAPPINGS = {"LyricSRTClipPlan": LyricSRTClipPlan, "LyricSRTClipPick": LyricSRTClipPick}
NODE_DISPLAY_NAME_MAPPINGS = {"LyricSRTClipPlan": "SRT → Clip Plan (뮤비 구간표)", "LyricSRTClipPick": "Clip Pick (구간+가사 프롬프트)"}
