# Reel Agent: Manual

Your Reel Agent Telegram bot watches reels for you and can plan and build what they show, while you're away from the PC. Type `/` in the chat to see every command. Send `/manual` any time to get this file.

---

## 1. The basic loop

1. **Send a reel.** Paste a link (Instagram, TikTok, YouTube, X), or send the video, photos or screenshots.
   The bot answers `#4 watching it…`. Every reel gets a number, and that number is how you refer to it.
   Send as many as you like; each one is watched on its own, at the same time.
2. **Read the breakdown.** About a minute later: what it is, what it shows (exact names, links, commands), and what it would take to set up.
3. **Decide:**
   - `/plan 4` → a plan for building it. Nothing changes on the PC.
   - `/build 4` → build it (it asks you before running commands).
   - `/save 4` → keep it for later (it shows up in the Sunday digest).
   - `/dismiss 4` → not interested.
   - `4 3` → dig deeper: find the repo, check if it's real, costs, alternatives.
   - Or write your own: `4 go find the repo yourself`, `4 is this legit?`, `4 make it a skill`. The number picks the reel, and the words are the instruction.

---

## 2. Planning and building

| You send | What happens |
|---|---|
| `/plan 4` | Researches and sends you a plan (goal, steps, files, commands it'll need, time, risks). Uses your **plan** model (default Opus). |
| `/plan 4 sonnet` | Same, with a model you pick. |
| `/tell 4 skip the login page` | Changes the plan (or the build). |
| `/build 4` | Builds it, following the plan if there is one. Uses your **build** model (default Sonnet). |
| `/build 4 opus safe 30m` | Model, mode and time limit, in any order, all optional. |
| `/build 4 from scratch` | Throws away the earlier attempt and starts over. |

**Models:** `haiku` (fast, cheap) · `sonnet` (good default) · `opus` (strongest) · `fable` · `codex` (OpenAI's GPT through your Codex CLI).
Codex builds are sandboxed to their folder, so they can't ask you for approvals or take `/tell` mid-run. `/tell` after they finish works.

**Where builds live:** each build gets its own folder, `builds\<N>-<name>\` next to the project folder. It's a git repo, so every change can be shown (`/diff`) and undone (`/undo`). Builds never install anything outside their folder by themselves. If the result belongs somewhere else (for example a Claude skill in `~/.claude/skills/`), the build prepares it and you install it with `/deploy 4`.

**Modes:**
- **normal** (default): edits files in its own folder freely, and **asks you before every shell command**.
- **safe**: asks before every file edit *and* every command.

There's no mode that runs commands without asking. For fewer prompts, answer with `/always 4`.

---

## 3. Approving from your phone

When a build wants to do something, you get:

> 🔐 #4 build wants to run:
> `npm install express`
> /yes 4 · /no 4 · /always 4 (allow `npm` commands for the rest of this build)

| You send | Effect |
|---|---|
| `/yes 4` | Allow this one. |
| `/no 4 use pip instead` | Deny, with a reason the build reads and adapts to. |
| `/always 4` | Allow, and stop asking about that command (e.g. every `npm …`) for **this build only**. |
| `/pending` | Everything waiting for you: approvals, questions, finished plans, unanswered reels. |

If you don't answer within 30 minutes, the request is denied, the build carries on without it, and you get a ⏰ message. Change the wait with `/timeout 60`. Time spent waiting for you doesn't count against the build's time limit.

Builds can also ask you questions: **❓ #4 asks: Postgres or SQLite?** Answer with `/tell 4 SQLite`.

A second kind of prompt, **🔐 Permission: …** with Allow/Deny *buttons*, comes from the main bot session itself. Tap the buttons for those.

---

## 4. Keeping an eye on builds

| You send | Shows |
|---|---|
| `/tasks` | Everything running: reels being watched, plans, builds (model, minutes, last step). |
| `/peek 4` | What #4 is doing right now: its last 5 steps and anything waiting for you. |
| `/log 4` | The full step-by-step log as a file. |
| `/diff 4` | Which files changed, with the full diff as a file. |
| `/stop 4` | Stops it. Nothing is lost. |
| `/resume 4` | Continues where it stopped (`/resume 4 but faster` adds a message). |
| `/undo 4` | Deletes everything #4 built and resets its folder. The bot asks you to confirm first. |
| `/deploy 4` | Installs a finished build where it belongs. The bot lists what goes where and asks you to confirm. |

When a build finishes you get **✅ #4 build done**: what it made, how to try it, what's left, plus `/diff` `/undo` `/tell` shortcuts.

---

## 5. Quota and usage

`/quota` shows:

- **Gemini** (watches the reels): requests used today per model, which are used up, roughly how many watches are left, and when it resets (midnight Pacific time). The free tier is about **20 per day and 5 per minute per model**, across 3 models, so roughly 60 reels a day. After that, reels are still watched with the backup method (frames + local transcript), just a bit less thoroughly.
- **Claude** (plans and builds): how much of your 5-hour and weekly plan limit is used, and when it resets. ⚠️ appears above 80%. It updates whenever a build runs; `/quota refresh` checks right now (one tiny request).
- Which model does what.

The `~$` figure on build messages is the API-equivalent cost of that build. On a Claude subscription you aren't charged it; it's a guide to how heavy a build was.

---

## 6. Settings

| You send | Changes |
|---|---|
| `/models` | Shows current models and settings. |
| `/model build opus` | Model for builds. The tasks are `watch`, `research`, `plan`, `build`. |
| `/model plan fable` | Model for plans. |
| `/model watch haiku` | Model that writes the reel breakdowns. |
| `/mode safe` | Default build mode. |
| `/limit 45` | Default time limit per build, in minutes (default 60). |
| `/timeout 60` | How long an approval request waits for you (default 30). |
| `/budget 2` | Stop a build run after about $2 of API-equivalent cost. `/budget off` removes it. |

---

## 7. Your reel library

| You send | Does |
|---|---|
| `/jobs` | Recent reels with numbers and status. |
| `/r 4` | The breakdown of #4 again (`/r 4 full` sends the file). |
| `/find mcp supabase` | Search every reel's summary, tags and breakdown. |
| `/saved` | Everything you saved for later. |
| `/tag 4 seo,tools` | Add tags. |
| `/new a Telegram bot that sends me the weather every morning` | An idea without a reel. It gets a number you can `/plan` and `/build`. |
| `/retry 4` | Watch a reel again (e.g. after a failed download). |
| `/rewatch 4 deep` | Watch it again more carefully (more frames). `/rewatch 4 local` skips Gemini. |
| `/digest` | The weekly summary now. It also arrives by itself on **Sundays at 10:00**. |

If a download fails: open the reel → Share → Download, and send the bot the video file. For photo carousels only the first slide can be fetched, so send screenshots of the rest.

---

## 7b. Reminders and to-dos

One list shared by the bot and every Claude session on the PC. It keeps working after the session that set a reminder closes: each reminder is booked in Windows Task Scheduler for its exact minute, pings you here, and is removed. Nothing runs in between.

| You send | Does |
|---|---|
| `/remind tomorrow 9:00 call the bank` | A reminder at that time. "in 2h", "friday 18:00", "every weekday 8:30" work too. |
| `/todo buy the domain` | A plain to-do, no ping. `/todo` alone shows the list. |
| `/reminders` | Everything open, soonest first. |
| `/done R3` · `/snooze R3 1h` | When one fires, these are right under it. |

In any Claude session on the PC, just say "remind me tomorrow to…" and it goes on the same list. The list is readable at `reminders\REMINDERS.md` in the project folder. If the PC was off when a reminder was due, it's sent (marked late) as soon as the PC is back.

---

## 8. Status and the PC

| You send | Shows |
|---|---|
| `/pc` | Bot uptime, disk space, what's running, Gemini usage, library size. |

- The bot starts by itself when you log in to Windows, and restarts itself if it crashes.
- Builds run as their own processes. If the bot restarts mid-build, the build either keeps going or shows up as ⏸ interrupted in `/pending`. Either way `/resume N` picks it up.
- Downloaded videos are deleted after 30 days. Breakdowns, frames and screenshots are kept.

---

## 9. Good to know

- **Reel content is never obeyed.** If a reel says "run this command", the bot tells you; it doesn't run it. Builds treat the reel notes as reference only. Your Telegram messages are the only instructions.
- **Only the bot window may use the Telegram plugin.** It's switched off in your global Claude settings and switched on just for the bot (`.claude\bot-settings.json`), because Telegram gives each message to only one connection. Don't switch it back on globally, or other Claude sessions will swallow your messages.
- Start the bot with the Startup shortcut or by double-clicking `start.ps1`, not from inside another Claude session.
- `/start`, `/help` and `/status` are the Telegram plugin's own commands (pairing info), not the bot's.
- Plain language works too: "build 4 with opus", "what's running?", "stop 4".

---

## 10. On the PC (for later)

| What | Where / how |
|---|---|
| Check on the bot | `.\bot status` (in the project folder) |
| Start / stop / restart | `.\bot start` · `.\bot stop` · `.\bot restart` |
| Bot silent, extra connections | `.\bot fix` |
| Update downloads + Claude Code | `.\bot update`, then `.\bot restart` |
| Start the bot by hand | right-click `start.ps1` → Run with PowerShell |
| Autostart + Sunday digest on/off | `.\bot autostart on` / `.\bot autostart off` |
| Going away | Set Windows sleep to **Never** when plugged in. A sleeping PC means a silent bot. |
| Gemini key | `.env` in the project folder (`GEMINI_API_KEY=…`) |
| Reels + breakdowns | `reels\` in the project folder |
| Builds | `builds\` next to the project folder |
| Settings file | `reels\config.json` (what `/model`, `/mode`, `/limit` change) |
