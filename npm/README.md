# reel-agent

**Send a reel to your Telegram bot. It tells you what the video really shows, then builds it on your PC with Claude Code, asking your phone before every command.**

![Reel Agent demo](https://raw.githubusercontent.com/HNF-FRN/Reel-watcher-telegram-Agent/main/docs/assets/chat-demo.gif)

## Set up in one command (Windows 10/11)

```
npx reel-agent
```

It walks you through everything and asks before changing anything:

1. **Tools:** installs Python, Git and Claude Code if they're missing (through `winget` and Anthropic's official installer), then signs you in to Claude.
2. **Download:** gets Reel Agent into `~/reel-agent`, or updates it if it's already there.
3. **Guided setup:** your Telegram bot token from [@BotFather](https://t.me/BotFather), a free [Gemini key](https://aistudio.google.com/apikey), video packages, reminders and start-at-login.
4. **Start and pair:** opens the bot, waits for your first message on Telegram, and approves your account from the code it sends you. No commands to type in the bot window.

You need a Claude Pro or Max plan and Telegram on your phone.

## Any OS: watch a video from the terminal

```
npx reel-agent watch https://www.instagram.com/reel/...
```

Gemini watches the whole video with sound and prints what it shows: the tools, links, repos and commands. Set `GEMINI_API_KEY` (free) for the full breakdown. Without it you get frames and a transcript. Files go to `reels/` in the current folder. It works on Windows, macOS and Linux and needs Python 3.10+ (it offers to install `yt-dlp` and `imageio-ffmpeg`).

## Manage the bot

```
npx reel-agent status      running? connected to Telegram? what's building?
npx reel-agent start       stop | restart | fix | update | tasks | quota
npx reel-agent pair [code] approve your Telegram account
npx reel-agent doctor      check the whole setup, change nothing
```

Add `--dir <folder>` to use an install somewhere other than `~/reel-agent`.

## Safety

- Nothing on your PC changes without a yes.
- Reel content is treated as information, never as instructions.
- Every shell command a build wants to run is sent to your phone first.
- Only the Telegram account you pair can use the bot.

## Links

[GitHub](https://github.com/HNF-FRN/Reel-watcher-telegram-Agent) · [Website](https://hnf-frn.github.io/Reel-watcher-telegram-Agent/) · [Setup guide (PDF)](https://github.com/HNF-FRN/Reel-watcher-telegram-Agent/blob/main/docs/Reel%20Agent%20Setup%20Guide.pdf) · [Issues](https://github.com/HNF-FRN/Reel-watcher-telegram-Agent/issues)

MIT license. Not affiliated with Anthropic, Google, Cloudflare or Telegram.
