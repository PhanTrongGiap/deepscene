# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**DeepScene** — a CLI tool that understands video by decomposing it into signals:
- `yt-dlp` downloads public/authenticated videos
- `ffmpeg` extracts representative frames and audio chunks
- Gemini (or an OpenAI-compatible endpoint) analyzes frames, transcribes audio, and synthesizes structured output

Two modes: `summary` (fast understanding) and `detail` (storyboard-grade reconstruction).

## Commands

```bash
# Install
./install.sh

# Usage
deepscene summary <url-or-local-video> [--frames N] [--out DIR] [--cookies FILE] [--format md|json]
deepscene detail  <url-or-local-video> [--frames N] [--out DIR] [--cookies FILE]

# Lower-level commands for debugging individual stages
deepscene-download <url>
deepscene-frames   <video>
deepscene-transcribe <audio>
deepscene-audio    <audio>
deepscene-vision   <video>
```

No build step. No test suite. Run commands directly.

## Configuration

Config lives at `~/.config/deepscene/env` (see `.env.example`). The loader in `lib/env.sh` reads that file on every invocation. Local `.env` loading requires `DEEPSCENE_LOAD_LOCAL_ENV=1`.

Key env vars:
- `GOOGLE_AI_KEY` — required for all Gemini operations
- `DEEPSCENE_GEMINI_MODEL` — default model (fallback: `gemini-2.5-flash`); overridden per-role by `DEEPSCENE_GEMINI_VISION_MODEL`, `DEEPSCENE_GEMINI_AUDIO_MODEL`, `DEEPSCENE_GEMINI_SYNTHESIS_MODEL`
- `DEEPSCENE_OPENAI_API_KEY` + `DEEPSCENE_OPENAI_BASE_URL` — optional OpenAI-compatible synthesis backend
- `DEEPSCENE_SERVER_MODE=1` — disables browser cookie autodetect
- `DEEPSCENE_CACHE_DIR` — default: `~/.cache/deepscene`

## Architecture

```
bin/deepscene              → dispatcher: routes summary|detail to sub-commands
bin/deepscene-summary      → sets DEEPSCENE_MODE=summary, execs lib/storyboard.py
bin/deepscene-detail       → sets DEEPSCENE_MODE=detail, execs lib/storyboard.py
bin/deepscene-download     → yt-dlp wrapper with 4-tier cookie fallback + URL validation
bin/deepscene-frames       → ffmpeg frame extraction
bin/deepscene-audio        → audio chunk extraction
bin/deepscene-transcribe   → ASR
bin/deepscene-vision       → frame → Gemini vision

lib/env.sh                 → shared env loader + cache dir helpers + URL validator
lib/storyboard.py          → core Python: frame sampling, Gemini/OpenAI calls, JSON synthesis, Markdown rendering

skills/deepscene/SKILL.md  → Claude Code skill definition (when to invoke, workflow, task-specific prompts)
prompts/                   → standalone prompt files (implement-from-video, extract-architecture, clone-ux, paper-to-code, tutorial-walkthrough)
docs/                      → cookies.md, platforms.md
```

### Data flow (`storyboard.py`)

1. `resolve_video` — local file or download via `deepscene-download`
2. `sample_frames` — scene-change detection (ffmpeg `select='gt(scene,…)'`) + evenly-spaced interval frames
3. `analyze_frames` → `gemini_generate` with base64-encoded frames → writes `vision.md`
4. `analyze_audio_chunks` → split audio into N-second chunks → `gemini_generate` per chunk → writes `audio_chunks.json`
5. `build_summary_json` / `build_storyboard_json` → prompt synthesis via `text_generate` (Gemini or OpenAI) → `parse_json_with_repair` → `normalize_storyboard`
6. Write artifacts (`summary.json`, `summary.md` or `storyboard.json`, `storyboard.md`) and print to stdout

If synthesis fails at step 5, a structured fallback is assembled from step 3's parsed observations (`fallback_summary_json` / `fallback_storyboard_json`).

### Security constraints (enforce these)

- Cache dirs are symlink-guarded, owner-checked, mode 700
- URLs validated against localhost, private IPs, link-local, multicast, reserved ranges (`deepscene_validate_url` in `lib/env.sh`)
- No local `.env` loading unless `DEEPSCENE_LOAD_LOCAL_ENV=1`
- Server mode disables browser cookie autodetect

## Key Implementation Notes

- `gemini_model(kind)` checks `DEEPSCENE_GEMINI_{KIND}_MODEL` first, then `DEEPSCENE_GEMINI_MODEL`, then hardcodes `gemini-2.5-flash`
- `synthesis_provider` defaults to `auto`: uses OpenAI if `DEEPSCENE_OPENAI_API_KEY` or `OPENAI_API_KEY` is set, otherwise Gemini
- JSON repair: if the model returns malformed JSON, `parse_json_with_repair` sends a repair prompt with `temperature=0.0`; on second failure it returns a structured fallback object with the raw text embedded
- Artifact caching: if `vision.md` or `audio_chunks.json` already exist in the output dir, they are reused (idempotent re-runs)
- Download caching: URL → SHA1 hash → fixed filename in `~/.cache/deepscene/videos/`; non-empty file = cache hit
