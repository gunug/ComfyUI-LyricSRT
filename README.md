# ComfyUI-LyricSRT

Turn a song into a timed lyric **SRT** inside ComfyUI, then fix the timing by ear in an in-node editor that plays the audio with the subtitles.

Built on top of [ComfyUI-Qwen3-ASR](https://github.com/kaushiknishchay/ComfyUI-Qwen3-ASR) (Qwen3-ASR + Qwen3-ForcedAligner word timestamps).

## Nodes

### Lyric Timestamps → SRT (`LyricTimestampsToSRT`)
Converts the `timestamps` output of **Qwen3 ASR Transcriber** (with a Forced Aligner connected) into SRT.

- **lyrics** (optional): paste the known lyrics, one subtitle per line. Section tags like `[verse1]`, `[chorus]` are ignored. The lines are mapped onto the ASR word timings with a global character alignment, so the subtitle text is always your exact lyrics even when the ASR misheard words, and repeated choruses stay in order.
- Empty **lyrics**: the recognised words are grouped into subtitles by `max_gap` (silence that starts a new line), `max_chars` and `max_seconds`.
- **end_pad**: seconds added after each line's last word (clipped to the next line).
- Duplicate words from the transcriber's chunk overlap are dropped.

### Lyric SRT Editor (`LyricSRTEditor`)
Output node with an editor in the node body:

- audio player + the current subtitle shown large, current row highlighted while playing
- per line: `▶` play from this line, `[` set start = playhead, `]` set end = playhead, `✕` delete, editable start / end / text
- add a line at the playhead, shift everything ±0.1 s, jump back 2 s, 0.5× speed
- `💾 저장` save to the shown path, `📁 다른 위치에 저장…` native Save-As dialog on the ComfyUI machine, `📂 폴더 열기` open the folder

Inputs:
- **filename**: empty = the loaded audio file's name (traced back through the `audio` link to the LoadAudio node)
- **save_dir**: empty = `output/lyric_srt`; relative = under `output/`; absolute (e.g. `D:\subs`) = used as-is

On every run the generated SRT is written as `<name>_auto.srt`; the editor saves your corrected version as `<name>.srt`. Outputs `srt` and `srt_path`. Edits are kept in the workflow, so reopening it restores them.

### SRT → Clip Plan (`LyricSRTClipPlan`)
Turns a lyric SRT into a music-video clip plan for 15-second video models (MiniMax H3 / FastH3 Omni).

- Time-based packing: each clip takes as many whole lines as fit in `max_clip_sec` (render handles included). A line that would cross the limit moves to the next clip, and every cut lands in the silence between two lines, so no word is cut in half.
- A silence of at least `instrumental_gap_sec` becomes its own instrumental clip (split if longer than the limit).
- Hum markers: an SRT line whose text is `[허밍]`, `(hum)`, `음~`, `mmm` … is not a lyric line but a hum event; the clip that contains it gets a timed hum in its prompt.
- Connect the song to `audio` so the last clip ends exactly at the song end.
- Outputs `plan` (JSON string), `clip_count` and a readable `summary`; also writes `output/<output_folder>/plan.json` for the merge tool.

### Clip Pick (`LyricSRTClipPick`)
Put one inside a per-clip subgraph. Give it the plan and a `clip_index`, plus your own `camera` and `action` text; it outputs the clip's `start_sec`, `length_sec`, `filename_prefix` and a full FastH3 Omni prompt (everything after `subject_definitions`):

- sung clips: the lines inside `<d>[Language] ...</d>` with `<Audio 1>` reused 1:1 (`fully_copy`) for exact lip-sync
- instrumental clips: written without any singing / voice wording, lips closed, so the lip-sync model does not mouth along to the music
- hum events: an explicit timeline, e.g. silent listening until `00:11.000`, then `<d>[Korean] 음~</d>` with closed lips

## Lyric music-video workflow

`example_workflows/lyric_mv_fasth3_omni.json` (needs the FastH3 Omni / MiniMax H3 models and ComfyUI's built-in MiniMax H3 nodes):

1. Make and correct the SRT with the first workflow (`lyric_srt_qwen3_asr.json`).
2. In the MV workflow pick the singer picture, the song, and the SRT path in **SRT → Clip Plan**; check the plan in the preview node.
3. One subgraph node per clip ("노래 클립 (Omni 립싱크)"): set `clip_index`, `camera`, `action`, `seed`. The subgraph cuts the song slice, builds the prompt and saves `mv_clips/clip_XX`. Mute the clips you do not want to render.
4. Merge: copy `tools/merge_mv.py` into `output/mv_clips/` and run `python merge_mv.py song.mp3`. It trims the render handles, joins the clips and lays the original song over the whole video, so the sound is never cut. Pin a specific take with `09=clip_09_00005_.mp4`; otherwise the newest take of each clip is used.

## Install

```
cd ComfyUI/custom_nodes
git clone https://github.com/gunug/ComfyUI-LyricSRT
git clone https://github.com/kaushiknishchay/ComfyUI-Qwen3-ASR
python -m pip install -r ComfyUI-Qwen3-ASR/requirements.txt
```

No extra Python dependencies for this pack (the Save-As dialog uses the standard-library `tkinter`).

Models (see the Qwen3-ASR pack) go in `models/diffusion_models/Qwen3-ASR/`:
- `Qwen/Qwen3-ASR-1.7B` → `Qwen3-ASR/Qwen3-ASR-1.7B/`
- `Qwen/Qwen3-ForcedAligner-0.6B` → `Qwen3-ASR/Qwen3-ForcedAligner-0.6B/`

Restart ComfyUI and hard-refresh the browser (Ctrl+Shift+R) so the editor UI loads.

## Example workflow

`example_workflows/lyric_srt_qwen3_asr.json`:

LoadAudio → Qwen3 ASR Transcriber (1.7B, language set, max_new_tokens 512) ← Qwen3 Forced Aligner Config (0.6B) → Lyric Timestamps → SRT → Lyric SRT Editor

On an RTX 4090 a 2:23 song transcribes and aligns in about 45 s.

## Notes

- The save endpoint only writes `.srt` files. The Save-As dialog opens on the machine running ComfyUI, so it is meant for a local setup.
- Built and tested with ComfyUI 0.44 and frontend 1.59 on Windows.

## License

MIT
