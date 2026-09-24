# Cloud mode: the same Telegram bot while your PC is off

Optional. Without it the bot works as before, only while the PC is on. With it, a free Cloudflare Worker stands
in for the PC bot whenever that isn't running, on the same bot and mostly the same commands.

## How it works

- **Nothing extra runs on your PC.** While the PC bot runs, its Telegram plugin collects messages within a second.
  Once a minute the Worker asks Telegram whether anything is waiting. If a message sits uncollected for about 40
  seconds (PC off, asleep, offline, or the bot window closed), the Worker points the bot at itself and says so
  on Telegram. When the PC bot starts again, its plugin takes the bot back automatically (it deletes the webhook on
  start). So only the first message after the PC goes off waits, up to ~2 minutes.
- **One library.** The PC sends each finished reel and every reminder change to the Worker as it happens. When the
  bot starts, it copies in whatever the cloud did meanwhile. Job and reminder numbers continue from the PC's.
- **Cheap work stays in the Worker:** commands, `/remind`, `/todo`, `/reminders`, `/done`, `/snooze`, and
  watching videos or photos sent to the bot and YouTube links (directly with Gemini's free tier).
- **Slow work uses a Claude Code cloud routine** on your Claude Pro or Max plan: Instagram/TikTok links (yt-dlp),
  `/plan`, `/build`, `/retry`, `/rewatch`, and free-text requests. Routines have a daily run cap (see
  claude.ai/code/routines), so each of these costs one run. Cloud builds push a branch and draft pull request to a
  **private** repository, since they cannot change a switched-off PC. `/yes N` and `/no N` approve their commands.
- **Reminders are sent once.** Your PC's Task Scheduler sends them as before, but asks the Worker first. If the PC
  is off, the Worker sends it (within 3 minutes of the time, or on time in cloud mode).
- PC-only commands (`/tell`, `/always`, `/diff`, `/log`, `/undo`, `/deploy`, `/quota`, settings) answer "only on
  the PC" instead of spending a routine run. `/pc` shows which side has the bot; `/failover off` stops the takeover.

Costs: Cloudflare Workers and D1 free plans (no card), Gemini's free API tier, and your existing Claude
subscription's routine runs. Nothing here enables a paid plan.

## Setup

You need Node.js, a free Cloudflare account and a Claude Pro or Max plan with routines.

### 1. Worker and database

From this folder:

```sh
npm install
npx wrangler login
npx wrangler d1 create reel-agent-cloud
cp wrangler.jsonc wrangler.local.jsonc
```

In `wrangler.local.jsonc` (git-ignored), put the `database_id` from the previous command. Then create the tables
(an existing database from the first cloud version: use `upgrade-2.sql` instead of `schema.sql`) and set the
secrets. Each `secret put` asks for the value, so nothing lands in your shell history or this repository:

```sh
npx wrangler d1 execute reel-agent-cloud --remote --file=schema.sql
npx wrangler secret put TELEGRAM_BOT_TOKEN --config wrangler.local.jsonc
npx wrangler secret put TELEGRAM_OWNER_ID --config wrangler.local.jsonc
npx wrangler secret put TELEGRAM_WEBHOOK_SECRET --config wrangler.local.jsonc
npx wrangler secret put BACKEND_TOKEN --config wrangler.local.jsonc
npx wrangler secret put GEMINI_API_KEY --config wrangler.local.jsonc
npm run deploy
```

`TELEGRAM_OWNER_ID` is your numeric Telegram user ID (the `allowFrom` number in
`~/.claude/channels/telegram/access.json`). `TELEGRAM_WEBHOOK_SECRET` and `BACKEND_TOKEN` are two different random
strings of 32+ letters, digits, `_` or `-`. Deploy prints the Worker's URL: put it in `PUBLIC_URL` in
`wrangler.local.jsonc` and run `npm run deploy` again.

### 2. Claude routine

At [claude.ai/code/routines](https://claude.ai/code/routines) create a **Cloud** routine with this repository and a
new **private** repository for build output (e.g. `reel-cloud-builds`), and paste [`ROUTINE.md`](ROUTINE.md) below
its line as the prompt. Remove connectors it doesn't need. Give it a cloud environment with:

- **Full** network access (reel downloads, the Worker);
- `REEL_CLOUD_URL` = the Worker URL, as a plain environment variable;
- an **API credential** for the Worker's hostname: header `Authorization`, prefix `Bearer`, value = your
  `BACKEND_TOKEN`. Claude's proxy adds it to requests, so the routine never sees it;
- setup script: `python -m pip install yt-dlp faster-whisper imageio-ffmpeg`.

Add an **API trigger**, then store its URL and token in the Worker:

```sh
npx wrangler secret put CLAUDE_ROUTINE_URL --config wrangler.local.jsonc
npx wrangler secret put CLAUDE_ROUTINE_TOKEN --config wrangler.local.jsonc
```

### 3. Connect the PC

```sh
python cloud/pc_link.py setup https://reel-agent-telegram.<your-subdomain>.workers.dev
```

This saves the URL in `reels/config.json` and sends your library and reminders to the Worker. From then on
`start.ps1` syncs at every start. To test: close the PC bot window, send the bot `/menu`, and within ~2 minutes the
cloud answers. Start the bot again and it takes over.

## Files

| File | Runs | Does |
|---|---|---|
| `worker.mjs` | Cloudflare | Telegram webhook, commands, reminders, Gemini watching, failover watchdog, `/backend/*` for routines, `/pc/*` for the PC |
| `schema.sql`, `upgrade-2.sql` | once | database tables |
| `pc_link.py` | your PC | sync with the Worker (called by `start.ps1`, `jobs.py`, `remind.py`) |
| `ROUTINE.md` | Claude routine | the routine's instructions |
| `client.py`, `watch.py` | Claude routine | report to the Worker; run `reel.py` on a link or Telegram file |
| `approve.py` | Claude routine | hook: every build shell command waits for `/yes N` on Telegram |
| `test_*.py`, `test_*.mjs` | tests | `npm test`, `python test_approve.py`, `python test_pc_link.py` |

Sources: [Workers limits](https://developers.cloudflare.com/workers/platform/limits/),
[D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/),
[Claude routines](https://code.claude.com/docs/en/routines),
[Telegram setWebhook / getWebhookInfo](https://core.telegram.org/bots/api#setwebhook).
