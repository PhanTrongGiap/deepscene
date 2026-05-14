# Prompt library

**DeepScene prompts** — turning storyboard artifacts into working code, architecture diagrams, and cheat sheets.

Five copy-paste prompts that turn `deepscene` storyboard output into a concrete artifact. Pick
the one that matches your goal, paste it above the `deepscene` block, hand
the whole thing to your agent.

Each prompt assumes you ran `deepscene detail <url>` and now have:

```text
STORYBOARD_MD: <path>
STORYBOARD_JSON: <path>
FRAMES: <frame-directory>
```

| File | When to use |
|---|---|
| [implement-from-video.md](implement-from-video.md) | Tutorial / coding walkthrough → working code |
| [extract-architecture.md](extract-architecture.md) | System / architecture talk → interactive diagram |
| [clone-ux.md](clone-ux.md) | UI / motion demo → working React component |
| [paper-to-code.md](paper-to-code.md) | ML paper or research talk → runnable notebook |
| [tutorial-walkthrough.md](tutorial-walkthrough.md) | Long tutorial → AI type-along, step by step |

Mix and match: nothing stops you from running the same `deepscene` output
through two prompts and comparing artifacts.
