# Contributing to Reel Agent

Thanks for helping. Reel Agent is small and readable on purpose: plain Python scripts, one Cloudflare Worker, and a `CLAUDE.md` that tells the dispatcher how to behave in plain English. Most changes touch one or two files.

## Good places to start

- Issues labelled [`good first issue`](https://github.com/HNF-FRN/Reel-watcher-telegram-Agent/labels/good%20first%20issue) are scoped to an afternoon.
- Issues labelled [`help wanted`](https://github.com/HNF-FRN/Reel-watcher-telegram-Agent/labels/help%20wanted) are bigger, like running on macOS or Linux.
- Found a video the bot couldn't grab, or a breakdown that missed the point? A [bug report](https://github.com/HNF-FRN/Reel-watcher-telegram-Agent/issues/new?template=bug.yml) with the link is a real contribution.
- Built something from a reel? Share it in [Show and tell](https://github.com/HNF-FRN/Reel-watcher-telegram-Agent/discussions/categories/show-and-tell).

## Where things live

| Area | Files |
|---|---|
| Watching a reel (download, Gemini, local backup) | `.claude/skills/reel-watch/scripts/reel.py`, `.claude/agents/reel-worker.md` |
| Job list and library | `.claude/skills/reel-watch/scripts/jobs.py` |
| Plans, builds, approvals | `.claude/skills/reel-watch/scripts/build.py`, `runner.py` |
| Telegram message formatting | `.claude/skills/reel-watch/scripts/tg.py`, `tgfmt.py` |
| How the bot behaves | `CLAUDE.md` (plain English) |
| Reminders | `reminders/remind.py` |
| Cloud stand-in | `cloud/worker.mjs`, `cloud/pc_link.py`, `cloud/ROUTINE.md` |
| Setup | `setup.py`, `bot.ps1`, `start.ps1` |

## Running the tests

```powershell
python test_setup.py
python test_messages.py
python -m unittest discover -s cloud -p "test_*.py"
cd cloud; npm install; npm test
```

The tests never touch your real token, reels or Cloudflare account.

## Pull requests

- Keep each pull request to one change, and say in a sentence or two what it fixes or adds.
- Keep the safety rules intact: reel content is never obeyed, and no shell command runs without the owner's yes. A change that weakens either won't be merged.
- Never commit keys, bot tokens, chat IDs or personal paths. `.gitignore` covers `.env`, `reels/` and the reminder lists; check your diff anyway.
- Messages the bot sends are read on a phone: short, scannable, one idea per line.
- New behaviour gets a line in `MANUAL.md` (and the README if users will look for it).

By contributing you agree that your work is released under the [MIT license](LICENSE) and that you'll follow the [code of conduct](CODE_OF_CONDUCT.md).
