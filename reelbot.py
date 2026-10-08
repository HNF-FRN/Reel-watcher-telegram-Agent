"""Start the Telegram bot that needs no Claude Code, on Windows, macOS or Linux:

    python reelbot.py              run the bot
    python reelbot.py pair CODE    approve your Telegram account
    python reelbot.py check        test the bot token

The bot itself is .claude/skills/reel-watch/scripts/reelbot.py, next to the scripts it runs.
"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parent / ".claude" / "skills" / "reel-watch" / "scripts" / "reelbot.py"),
               run_name="__main__")
