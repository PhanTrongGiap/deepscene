# Security Policy

DeepScene processes untrusted video URLs and optional authentication cookies.
Treat it as a network-facing downloader when running on servers.

## Supported Version

Security fixes target the `main` branch.

## Reporting

**Preferred:** Use [GitHub Security Advisories](https://github.com/PhanTrongGiap/deepscene/security/advisories/new) to report privately before public disclosure.

For non-sensitive bugs, open a regular GitHub issue and label it `security`.

**In scope:**
- URL injection or SSRF via the video URL input
- Path traversal in `--out` or `--cookies` arguments
- Cookie file leaks to third-party URLs
- Cache directory privilege escalation

**Out of scope:**
- API rate limits or cost overruns from normal use
- yt-dlp or ffmpeg upstream vulnerabilities (report to those projects directly)
- Denial-of-service from very long videos (use `DEEPSCENE_MAX_VIDEO_SECONDS`)

Do not include API keys, browser cookies, or private video URLs in public reports.

## Operational Guidance

- Do not commit provider keys. Store them in `~/.config/deepscene/env` or process
  environment variables.
- Set `DEEPSCENE_SERVER_MODE=1` for unattended jobs and servers.
- Prefer explicit `--cookies <file>` on servers instead of browser cookie
  autodetection.
- Use `DEEPSCENE_MAX_VIDEO_SECONDS`, `DEEPSCENE_MAX_DOWNLOAD_MB`, and
  `DEEPSCENE_COMMAND_TIMEOUT_SEC` to bound resource usage.
