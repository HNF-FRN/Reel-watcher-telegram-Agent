# Cloud mode for the same Telegram bot

This mode keeps the existing bot token and commands available while your PC is
off. A Cloudflare Worker accepts Telegram messages, stores jobs in D1, and
starts a Claude Code cloud routine for watching, planning, and building.
The routine uses your existing Claude Pro or Max allowance. It is **not** a
standalone free Claude service: if that subscription or its routine allowance
ends, cloud work pauses. Cloudflare's free plan and Gemini's free API tier do
not require a payment card, but their limits apply.

Cloud builds run in Anthropic's temporary cloud environment and create a
GitHub branch or draft pull request. They cannot install software on a PC
that is switched off. `/undo` and `/deploy` require manual review in this
cloud version. PC mode retains its original behavior when you switch back.

## 1. Create the free Worker and database

Create a free Cloudflare account, without adding a card. On a computer with
Node.js, from this directory:

```sh
npx wrangler login
npx wrangler d1 create reel-agent-cloud
```

Copy the returned `database_id` into `wrangler.jsonc`, then run:

```sh
npx wrangler d1 execute reel-agent-cloud --remote --file=schema.sql
```

Set each Worker secret interactively; do not commit any value:

```sh
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_OWNER_ID
npx wrangler secret put TELEGRAM_WEBHOOK_SECRET
npx wrangler secret put BACKEND_TOKEN
npx wrangler secret put CLAUDE_ROUTINE_URL
npx wrangler secret put CLAUDE_ROUTINE_TOKEN
npx wrangler deploy
```

Generate `TELEGRAM_WEBHOOK_SECRET` and `BACKEND_TOKEN` as separate random
values of at least 32 characters. `TELEGRAM_OWNER_ID` is your numeric Telegram
user ID, not the bot ID. Store the Worker URL shown by Wrangler.

## 2. Create the Claude cloud routine

At [Claude Code routines](https://claude.ai/code/routines), create a **Cloud**
routine connected to this repository's default branch. Paste the full
[`ROUTINE.md`](ROUTINE.md) prompt. Add an API trigger and copy its generated
URL and bearer token into the two corresponding Worker secrets above. The
token is an API trigger token for the routine, not an Anthropic Console API
key. Add only the GitHub connector if the routine needs to push branches or
open pull requests; remove unrelated default connectors.

Create a cloud environment with **Full network access**. Add the following
environment secrets or API credentials:

- `REEL_CLOUD_URL`: your deployed Worker base URL, without `/telegram`.
- `REEL_CLOUD_BACKEND_TOKEN`: the same value as Worker `BACKEND_TOKEN`.
- `GEMINI_API_KEY`: optional Google AI Studio free tier key for video analysis.

For the environment setup script, use:

```sh
python -m pip install -r requirements.txt
```

The routine starts from the repository default branch. Merge the cloud-mode
code before enabling the routine so the `cloud/` bridge and approval hook are
present in its checkout. Project hooks must be enabled for the Telegram
shell-command approval flow. Inspect one routine run's hook output before
using `/build` for meaningful work.

## 3. Move this bot's Telegram connection to the cloud

Stop the local Claude Telegram channel or any other process that polls this
bot. Telegram allows a webhook or `getUpdates` polling, not both. Put the
Worker URL, bot token, and webhook secret in local environment variables and
run `python cloud/setup_webhook.py`. The script does not print the secrets.
Send `/menu`, then `/new test idea`, then `/plan 1` to check the path. Send a
small public video or image to check watching. For a build, use `/build N` and
answer each `🔐` prompt with `/yes N` or `/no N`.

Keep the local channel off while the webhook is active. To switch back to PC
mode, delete the webhook through Telegram's Bot API before starting the local
poller. Telegram updates arriving while neither consumer is active may expire.

## Limits and behavior

- Cloudflare Workers and D1 have free quotas. If exhausted, the webhook may
  fail until limits reset. No paid plan is enabled by this setup.
- Claude cloud routines have daily run and subscription usage limits, and the
  API is in research preview. One reel, plan, or build starts one routine run.
- Gemini free API models have independent quotas; if unavailable, the watcher
  falls back to local video processing in the cloud session where possible.
- Video sites may block server-side downloads. Send the media file directly to
  Telegram when a link cannot be fetched.
- The cloud job list is in D1. It does not automatically import `reels/jobs.json`
  from the Windows PC, so old local job numbers are not available in cloud mode.
- `/tell`, `/always`, advanced local build settings, Windows reminder tasks,
  and arbitrary PC deployment are not implemented in cloud mode. The basic
  send/watch, `/new`, `/plan`, `/build`, `/yes`, `/no`, `/jobs`, `/r`, `/stop`,
  `/resume`, and reminders flow is implemented. `/stop` is cooperative.

Sources: [Workers free plan](https://developers.cloudflare.com/workers/platform/pricing/),
[D1 free plan](https://developers.cloudflare.com/d1/platform/pricing/),
[Claude routines](https://code.claude.com/docs/en/routines), and
[Telegram webhooks](https://core.telegram.org/bots/api#setwebhook).

