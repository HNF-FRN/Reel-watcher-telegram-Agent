# Security policy

Reel Agent runs code on your computer, so security reports are taken seriously.

## Reporting a vulnerability

Please **don't open a public issue**. Report it privately instead:
[Security → Report a vulnerability](https://github.com/HNF-FRN/Reel-watcher-telegram-Agent/security/advisories/new).

Include what an attacker could do, and the steps or the reel/message that triggers it. You'll get an answer within a week.

## What counts

Especially welcome:

- A reel, caption, transcript or web page that gets the bot or a build to **follow its instructions** (prompt injection).
- A way to **run a shell command without the owner's `/yes`**, or to write outside a build's folder without asking.
- A way for **someone other than the paired Telegram account** to control the bot or the cloud Worker.
- Anything that **leaks the bot token, the Gemini key**, or the owner's reels and reminders.

Only the latest version on `main` is supported.

## How it's built to resist that

- **No model routes messages in `reelbot.py`.** Commands are matched by fixed rules; reel content is only ever shown to you, never acted on.
- **Breakdowns quote, they don't generate.** Commands, repos and links come from OCR and the transcript (`extract.py`). A model only writes the summary, and reel content reaches it marked as data.
- **Every shell command of every build waits for `/yes`** (`runner.py`), whichever model builds: there is no unattended mode. `/always N` allows one command word for one build.
- **A build can't approve itself.** No build may write into its own `.git` (a hook or `core.fsmonitor` would run code at the runner's next commit) or `.reel` folder (where your answers are written), whether it is built by Claude, Codex or an open model.
- **The open-model agent is boxed in** (`agent.py`): it reads and writes only inside its build folder, and fetches only public internet addresses (redirects included), so a prompt-injected page can't make it read your files or your local network.
- **Strangers get nothing.** Before anyone is paired, a message gets a pairing code that does nothing until you approve it at the computer (`python reelbot.py pair <code>`); after that, the bot ignores every account but yours.
