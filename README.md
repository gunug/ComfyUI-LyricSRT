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
