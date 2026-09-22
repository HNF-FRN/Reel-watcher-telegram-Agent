# Fix: the bot never replies with a pairing code

**Symptom.** You finished setup, ran `.\bot start`, messaged the new bot on Telegram, and nothing came back: no pairing code, no reply at all.

**Status.** Fixed on 2026-09-22. `setup.py` now saves the token correctly and repairs old files, and `start.ps1` repairs the file before every launch. New setups and existing ones on any PC are covered.

## How it was found

1. **Is anything polling Telegram?** `.\bot status` said `Telegram: not connected`. The bot's `claude.exe` (the one started with `--channels plugin:telegram…`) was running, but **no `bun.exe` process existed**. The Telegram plugin is a bun server, so it had never started, and nobody was reading the messages.
2. **Is the plugin logic at fault?** No. With no `~/.claude/channels/telegram/access.json`, the plugin defaults to `dmPolicy: "pairing"` and replies with a code to anyone who writes. It never got that far.
3. **Why didn't the server start?** Reproduce the bot's launch with a debug log:
   ```powershell
   claude -p "reply OK" --settings .claude\bot-settings.json --debug-file $env:TEMP\dbg.log
   Select-String -Path $env:TEMP\dbg.log -Pattern telegram
   ```
   The log showed the server exiting right away:
   `telegram channel: TELEGRAM_BOT_TOKEN required … set in ~\.claude\channels\telegram\.env`.
   But the file existed and contained the token.
4. **Why couldn't it read the file?** The plugin (`server.ts`) splits the file on `\n` and matches each line with `/^(\w+)=(.*)$/`. In JavaScript, `.` does not match `\r`. `setup.py` saved the token with Python's `write_text(...\n)`, which on Windows writes `\r\n`. The line became `TELEGRAM_BOT_TOKEN=…\r`, the regex failed, and the plugin saw no token. A UTF-8 BOM, which Notepad or PowerShell 5's `Set-Content -Encoding UTF8` can add, breaks the `^\w+` match in the same way.
5. **Why did setup look fine?** Setup only checked that the text `TELEGRAM_BOT_TOKEN=` appeared in the file. The Python scripts (the `/` menu, reminders) strip whitespace, so they read the token fine, and setup's last step passed. Only the plugin's stricter parser failed.

## The fix

| Where | What |
|---|---|
| `setup.py` | Writes the token with `newline="\n"`. If the file already has `\r` or a BOM, a normal run rewrites it and `--check` reports it. |
| `start.ps1` | Before starting Claude, rewrites the token file as UTF-8 without BOM, with LF line endings, if needed. This also covers tokens pasted in Notepad. Warns if no token is saved. |
| `bot.ps1 status` | When the bot runs but Telegram is not connected, it says the plugin didn't start and points here. |

## If it happens again

1. Run `.\bot restart`. `start.ps1` repairs the token file on launch.
2. Run `.\bot status`. You should see `Telegram: connected`. Then message the bot: the pairing code should arrive within a few seconds.
3. Still not connected? Run the debug command from step 3 above and read the `telegram` lines. Common causes:
   - **Invalid or revoked token:** get a new one from @BotFather and run `.\setup`.
   - **`409 Conflict`:** another Claude session holds the bot. Run `.\bot fix`.
   - **bun not found:** run `npm install -g bun`.
