# Security policy

Reel Agent runs code on your PC, so security reports are taken seriously.

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
