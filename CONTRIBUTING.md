# Contributing to Reel Agent

Thanks for helping. Reel Agent is small and readable on purpose: plain Python scripts (standard library only, apart from the video tools), one Cloudflare Worker, and a `CLAUDE.md` that tells the Claude Code dispatcher how to behave in plain English. Most changes touch one or two files.

## Good places to start

- Issues labelled [`good first issue`](https://github.com/HNF-FRN/Reel-watcher-telegram-Agent/labels/good%20first%20issue) are scoped to an afternoon.
- Issues labelled [`help wanted`](https://github.com/HNF-FRN/Reel-watcher-telegram-Agent/labels/help%20wanted) are bigger, like running on macOS or Linux.
- Found a video the bot couldn't grab, or a breakdown that missed the point? A [bug report](https://github.com/HNF-FRN/Reel-watcher-telegram-Agent/issues/new?template=bug.yml) with the link is a real contribution.
- Built something from a reel? Share it in [Show and tell](https://github.com/HNF-FRN/Reel-watcher-telegram-Agent/discussions/categories/show-and-tell).

## Where things live

All scripts below are in `.claude/skills/reel-watch/scripts/` unless a path says otherwise.

| Area | Files |
|---|---|
| The bot without Claude Code (routing, pairing, cards) | `reelbot.py` (started by `reelbot.py` in the project root) |
| Watching a reel (download, frames, transcript) | `reel.py` |
| The open engine: OCR, extraction, breakdown | `ocr.py`, `extract.py`, `analyze.py` |
| Local models (any OpenAI-compatible server) | `llm.py` |
| Plans, builds, approvals | `build.py`, `runner.py`, and `agent.py` for open models |
| Checking what a reel names (`/deeper`) | `verify.py` |
| Job list and library | `jobs.py` |
| Telegram message formatting | `tg.py`, `tgfmt.py` |
| The Claude Code dispatcher | `CLAUDE.md` (plain English), `.claude/agents/reel-worker.md` |
| Reminders | `reminders/remind.py` |
| Cloud stand-in | `cloud/worker.mjs`, `cloud/pc_link.py`, `cloud/ROUTINE.md` |
| Setup | `setup.py`, `bot.ps1`, `start.ps1` (Windows), `npm/` (`npx reel-agent`) |

## Running the tests

```bash
pip install -r requirements.txt
python -m unittest discover -s . -p "test_*.py"      # everything below runs on any OS, offline
python -m unittest discover -s cloud -p "test_*.py"
cd cloud && npm install && npm test
```

The unit tests fake what they need: OCR and models in `test_analyze.py`, a model server in `test_agent.py`, the Telegram Bot API and a model in `test_reelbot.py` (which runs the whole bot end to end). They never touch your real token, reels or Cloudflare account.

The end-to-end tests of the open engine need ffmpeg, espeak-ng, an OCR engine and faster-whisper (`pip install -r requirements-open.txt`), and run a real open model if you name one:

```bash
REEL_E2E=1 python -m unittest test_open_core_e2e -v
REEL_E2E=1 REEL_LLM_MODEL=qwen2.5:3b python -m unittest test_open_core_e2e -v     # with Ollama running
```

CI runs all of it on every push ([tests.yml](.github/workflows/tests.yml)) and uploads the breakdowns and the build log it produced.

## Pull requests

- Keep each pull request to one change, and say in a sentence or two what it fixes or adds.
- Keep the safety rules intact: reel content is never obeyed, no shell command runs without the owner's yes, and nothing quoted in a breakdown comes from a model. A change that weakens any of them won't be merged.
- Everything has to keep working with open models and no account: a feature that needs a closed service is an option next to the open path, not a replacement for it.
- Never commit keys, bot tokens, chat IDs or personal paths. `.gitignore` covers `.env`, `reels/` and the reminder lists; check your diff anyway.
- Messages the bot sends are read on a phone: short, scannable, one idea per line.
- New behaviour gets a line in `MANUAL.md` (and the README if users will look for it).

By contributing you agree that your work is released under the [MIT license](LICENSE) and that you'll follow the [code of conduct](CODE_OF_CONDUCT.md).
