# DeepScene Security Best-Practices Report

DeepScene is a Bash/Python CLI that downloads videos with `yt-dlp`, extracts
frames/audio with `ffmpeg`, and sends selected media artifacts to configured AI
providers for transcription, audio understanding, visual analysis, and
storyboard synthesis.

## Current Mitigations

- Local `.env` files are ignored by default. They load only when
  `DEEPSCENE_LOAD_LOCAL_ENV=1`.
- Runtime artifacts default to `${XDG_CACHE_HOME:-$HOME/.cache}/deepscene`
  with owner checks, `0700` permissions, and symlink rejection.
- URL downloads are restricted to HTTP(S) and reject localhost, private IPs,
  link-local, multicast, reserved, and unspecified addresses.
- `DEEPSCENE_SERVER_MODE=1` disables browser-cookie autodetection for
  unattended workers.
- All AI provider calls use HTTPS with API keys in headers, not URLs.
- Large media guardrails are available through
  `DEEPSCENE_MAX_VIDEO_SECONDS`, `DEEPSCENE_MAX_DOWNLOAD_MB`, and
  `DEEPSCENE_COMMAND_TIMEOUT_SEC`.
- Gemini/OpenAI-compatible calls keep API keys in headers, not URLs.

## Residual Risks

- Browser cookie autodetection is powerful and should not be enabled for
  untrusted unattended jobs. Use `DEEPSCENE_SERVER_MODE=1` and explicit
  `--cookies <file>` on servers.
- AI providers receive the selected frames/audio chunks. Do not use production
  secrets, private customer media, or regulated content unless the configured
  provider account is approved for that data.
- `yt-dlp` platform behavior changes frequently. Keep it updated and treat
  extraction failures as normal operational events.
- Storyboard/audio outputs can hallucinate. Treat generated descriptions as
  analysis aids, not authoritative evidence.

## Recommended Server Defaults

```bash
DEEPSCENE_SERVER_MODE=1
DEEPSCENE_CACHE_DIR=/var/cache/deepscene
DEEPSCENE_MAX_VIDEO_SECONDS=7200
DEEPSCENE_MAX_DOWNLOAD_MB=1024
DEEPSCENE_COMMAND_TIMEOUT_SEC=1800
```

Keep provider keys in `~/.config/deepscene/env` or a secret manager. Do not
commit API keys to the repository.
