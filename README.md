# DeepScene

**Open-source video-to-storyboard CLI agent. Deep video understanding at 10× lower cost.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: macOS / Linux](https://img.shields.io/badge/platform-macOS%20%2F%20Linux-lightgrey)](#install)
[![Model: Gemini Flash](https://img.shields.io/badge/model-Gemini%20Flash-orange)](#setup)

Decompose any video into frames + audio chunks, run Gemini Flash over each piece, synthesize a structured storyboard. No GPU. No bloat. Pipeable JSON output for agent workflows.

---

## Quick Start

```bash
# Summarize a YouTube video
deepscene summary https://youtube.com/watch?v=...

# Full storyboard reconstruction, 24 frames
deepscene detail /tmp/video.mp4 --frames 24

# Pipe structured output into a downstream agent
deepscene summary <url> --format json | jq '.summary'
```

Install in one line:

```bash
curl -fsSL https://raw.githubusercontent.com/PhanTrongGiap/deepscene/main/install.sh | bash
```

Set one env var and go:

```bash
echo "GOOGLE_AI_KEY=..." >> ~/.config/deepscene/env
```

---

## How It Works

DeepScene never sends a full video to an LLM. It decomposes the video into the smallest useful signals, then synthesizes them.

```
Raw video URL
      │
      ▼
 deepscene-download          ← yt-dlp, cookie fallback, URL validation
      │
      ▼
 deepscene-frames            ← ffmpeg scene-change + interval sampling
      │
      ├─► deepscene-audio    ← ffmpeg chunk → Gemini Flash audio
      │
      ▼
 deepscene-vision            ← base64 frames → Gemini Flash vision → vision.md
      │
      ▼
 Synthesis (Gemini / OpenAI) ← vision.md + audio_chunks → storyboard.json + storyboard.md
      │
      ▼
 stdout (--format json)      ← pipe into any agent, MCP tool, or downstream script
```

**Frame selection is not uniform.** ffmpeg scene-change detection picks frames at visual cut points — the moments with the most new information. You get 12 frames that cover the full content arc, not 12 frames that oversample a static slide.

**Audio runs separately.** Each chunk is analyzed independently by Gemini Flash, giving you per-chunk speech, mood, and sound cues rather than one compressed transcript of the whole video.

**Runs are idempotent.** `vision.md` and `audio_chunks` are cached between runs. Re-running with different synthesis parameters costs nothing in tokens.

---

## Cost

| Method | 5 min video | 1 hr video |
|---|---|---|
| **DeepScene + Gemini Flash** | **~$0.003** | **~$0.04** |
| Raw video → Gemini 3 Flash (native) | ~$0.05 | ~$0.60 |
| Raw video → GPT-4V | ~$0.08 | ~$1.00 |

The gap comes from what gets sent. DeepScene sends sampled frames at base64 + short audio chunks. Native video APIs receive the full bitstream. For a 5-minute video, that is roughly a 16× reduction in token volume before any model pricing difference.

---

## Agent Workflows

`--format json` makes every DeepScene command a composable step in a larger pipeline:

```bash
# Feed video understanding into a planning agent
deepscene summary <url> --format json \
  | jq '{summary: .summary, shots: .shots}' \
  | your-agent --stdin

# Chain detail output into a code-gen step
deepscene detail <url> --frames 24 --out /tmp/scene \
  && cat /tmp/scene/storyboard.json | code-agent implement
```

### Claude Code skill

A ready-made Claude Code skill lives at [`skills/deepscene/SKILL.md`](skills/deepscene/SKILL.md). Drop it into any Claude Code project and Claude will automatically invoke DeepScene when you share a video URL and ask it to watch, implement, clone, or analyze.

Triggers on phrases like:
- "Watch this and explain how it works: `<url>`"
- "Implement what's in this video"
- "Clone this UI"
- "Extract the architecture from this talk"

---

## Compared to Alternatives

| | **DeepScene** | Video-LLaMA / Video-ChatGPT | VideoAgent | Whisper + LLaVA (DIY) | yt-dlp alone |
|---|---|---|---|---|---|
| Architecture | Decomposed pipeline: download → frames → audio chunks → LLM synthesis | Monolithic end-to-end model | Agent loop over frames | Manual glue code | Download only |
| Frame selection | ✅ Scene-change detection (ffmpeg) | Uniform sampling | Uniform sampling | Manual / uniform | — |
| Structured output | ✅ JSON storyboard: shots, scenes, characters, audio_cues | Free-form text | Free-form text | Free-form text | — |
| GPU required | ✅ No — API-only | ❌ A100/H100 | ❌ Local GPU | ⚠️ Partial (Whisper local) | ✅ No |
| Cost / 5 min video | ✅ ~$0.003 | ~$0–$5 (cloud GPU time) | ~$0.05–$0.20 | ~$0.01–$0.05 | $0 |
| Agent-pipeline ready | ✅ `--format json`, pipeable CLI | ❌ Notebook/demo only | ⚠️ Partial | ❌ | ❌ |
| Idempotent re-runs | ✅ Caches vision.md + audio chunks | ❌ | ❌ | ❌ | ✅ |

**When to use what:**

- **DeepScene** — structured JSON output, no GPU, CLI or agent workflow, any public video URL
- **Video-LLaMA / Video-ChatGPT** — open-ended conversational QA over video, local GPU available
- **DIY Whisper + LLaVA** — fully offline processing, comfortable writing your own glue code

---

## Install

```bash
git clone https://github.com/PhanTrongGiap/deepscene ~/.deepscene
cd ~/.deepscene && ./install.sh
```

**Dependencies:**

```bash
# macOS
brew install yt-dlp ffmpeg jq

# Debian / Ubuntu
sudo apt install yt-dlp ffmpeg jq python3 curl
```

The installer creates `~/.config/deepscene/env` and symlinks all commands into `~/.local/bin`.

---

## Setup

Edit `~/.config/deepscene/env`:

```bash
GOOGLE_AI_KEY=...
```

That is the only required key. Gemini Flash handles vision, audio, and synthesis by default.

**Model overrides** (optional):

```bash
DEEPSCENE_GEMINI_VISION_MODEL=gemini-2.5-flash
DEEPSCENE_GEMINI_AUDIO_MODEL=gemini-2.5-flash
DEEPSCENE_GEMINI_SYNTHESIS_MODEL=gemini-2.5-flash
```

**Swap the synthesis model** to OpenAI:

```bash
deepscene summary <url> --synthesis-provider openai
```

**Server / CI config:**

```bash
DEEPSCENE_SERVER_MODE=1          # disables browser cookie autodetect
DEEPSCENE_CACHE_DIR=/var/cache/deepscene
DEEPSCENE_MAX_VIDEO_SECONDS=7200
DEEPSCENE_MAX_DOWNLOAD_MB=1024
```

---

## Commands Reference

### `deepscene summary`

```
deepscene summary <url-or-path> [options]
```

Fast video understanding. Prints a Markdown summary to stdout. Writes artifacts when `--out` is set.

| Option | Default | Description |
|---|---|---|
| `--frames N` | 8 | Number of frames to sample |
| `--out DIR` | — | Write artifacts to this directory |
| `--cookies FILE` | — | Netscape cookies.txt for login-walled videos |
| `--format md\|json` | md | Output format. `json` is pipeable. |
| `--audio-chunk-sec N` | — | Duration of each audio chunk |
| `--synthesis-provider auto\|gemini\|openai` | auto | Model for final synthesis step |
| `--vision-model MODEL` | — | Override Gemini vision model |
| `--audio-model MODEL` | — | Override Gemini audio model |
| `--synthesis-model MODEL` | — | Override synthesis model |

**Output files:**
- `summary.md`, `summary.json`
- `vision.md`, `frames.json`, `audio_chunks.json`

---

### `deepscene detail`

```
deepscene detail <url-or-path> [options]
```

Storyboard-grade reconstruction. Runs the full pipeline and writes all intermediate artifacts. Accepts the same options as `summary`.

**Output files:**
- `storyboard.md`, `storyboard.json`
- `vision.md`, `frames.json`, `audio_chunks.json`
- Extracted frame directory and audio chunk directory

---

### Lower-level commands

These exist for debugging individual pipeline stages. Normal usage starts with `summary` or `detail`.

| Command | What it does |
|---|---|
| `deepscene-download` | Download video via yt-dlp |
| `deepscene-frames` | Extract frames via ffmpeg |
| `deepscene-audio` | Chunk + analyze audio via Gemini Flash |
| `deepscene-vision` | Run vision analysis on extracted frames |
| `deepscene-transcribe` | Transcribe audio chunks |

---

## Output Shape

`storyboard.json` (from `detail`) and `summary.json` (from `summary`) follow this structure:

```json
{
  "summary": "...",
  "shots": [...],
  "scenes": [...],
  "characters": [...],
  "audio_cues": [...]
}
```

The `--format json` flag on any command emits this to stdout, making it directly pipeable into `jq`, agent tools, or MCP servers.

---

## Login-Walled Videos

Most YouTube, TikTok, Reddit, Vimeo, and public X/Twitter posts work without cookies.

**Platforms that require auth:**

| Platform | No cookie | Auto browser | `--cookies FILE` |
|---|---|---|---|
| YouTube (public) | ✅ | — | — |
| YouTube (members-only) | ❌ | ✅ | ✅ |
| TikTok (public) | ✅ | — | — |
| X / Twitter (public) | ✅ | — | — |
| X / Twitter (sensitive) | ❌ | ✅ | ✅ |
| LinkedIn (most posts) | ❌ | ✅ | ✅ |
| Reddit (public) | ✅ | — | — |
| Facebook (public pages) | ✅ | — | — |
| Facebook (groups, private) | ❌ | ✅ | ✅ |
| Vimeo (public) | ✅ | — | — |
| Instagram (public reels) | ⚠️ inconsistent | ✅ | ✅ |

**Option 1 — browser session (interactive use):**

Sign in to the platform in Chrome, Firefox, Safari, Edge, Brave, or Chromium. DeepScene reads the live session directly via yt-dlp. No extensions, no DevTools, no cookie export.

```bash
DEEPSCENE_BROWSER=firefox deepscene summary <url>
```

**Option 2 — cookie file (servers, CI):**

Export a Netscape-format `cookies.txt` and pass it explicitly:

```bash
deepscene summary <url> --cookies ~/cookies.txt
```

Export from Chrome in one command:

```bash
yt-dlp --cookies-from-browser chrome --cookies ~/yt-cookies.txt \
       --skip-download "https://www.linkedin.com"
```

Set `DEEPSCENE_SERVER_MODE=1` to disable browser autodetect on unattended servers.

Full cookie setup guide: [docs/cookies.md](docs/cookies.md)

---

## Security Defaults

- Local `.env` loading is disabled unless `DEEPSCENE_LOAD_LOCAL_ENV=1`
- Cache directories are private, owner-checked, and symlink-guarded
- URL downloads reject localhost, private IPs, link-local, multicast, reserved ranges, and non-HTTP(S) targets
- Server mode disables browser cookie autodetection
- Cookies never leave your machine — the data path is: browser profile → yt-dlp (local process) → platform CDN

---

## License

MIT. See [LICENSE](LICENSE).
