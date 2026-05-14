# Platform support

`DeepScene` works on any platform `yt-dlp` understands. The table below
covers the ones we have explicitly tested and the cookie tier they need.

| Platform | No cookie | Auto browser | Manual `--cookies` |
|---|---|---|---|
| **YouTube** (public) | ✅ | — | — |
| **YouTube** (members-only) | ❌ | ✅ | ✅ |
| **TikTok** (public) | ✅ | — | — |
| **Reddit** (public posts) | ✅ | — | — |
| **Vimeo** (public) | ✅ | — | — |
| **X / Twitter** (public posts) | ✅ | — | — |
| **X / Twitter** (sensitive / blocked accounts) | ❌ | ✅ | ✅ |
| **LinkedIn** (most posts) | ❌ | ✅ | ✅ |
| **Facebook** (public pages) | ✅ | — | — |
| **Facebook** (groups, private posts) | ❌ | ✅ | ✅ |
| **Instagram** (public reels) | ⚠️ inconsistent | ✅ | ✅ |
| **Patreon** (paid) | ❌ | ✅ | ✅ |

Legend: ✅ works · ❌ blocked · ⚠️ partial · — not needed

## Tested versions

This matrix was last verified against:

- `yt-dlp` 2024.12.x (any recent build is fine)
- macOS 14, Linux Debian 12

If a platform fails on your machine, first run:

```bash
yt-dlp -U   # update yt-dlp to latest
```

Most platform breakages are fixed by a `yt-dlp` update — the project
ships fast (often weekly) for new platform changes.

## Adding a new platform

There's nothing to add. Any URL `yt-dlp` supports works in `DeepScene`
out of the box. The full list of 1,800+ supported sites lives at
[yt-dlp/supportedsites.md](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).
