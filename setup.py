"""setup.py - guided setup for Reel Agent. Run it with:  .\\setup   (or: python setup.py)

Every step checks first, explains what it will change, and asks before changing anything. Run it again any
time: finished steps are skipped. `python setup.py --check` only reports, changing nothing.
"""
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
TG_DIR = HOME / ".claude" / "channels" / "telegram"
PLUGIN = "telegram@claude-plugins-official"
CHECK = "--check" in sys.argv
problems = []


def say(msg=""):
    print(msg, flush=True)


def ok(msg):
    say(f"  [ok]   {msg}")


def todo(msg):
    say(f"  [todo] {msg}")
    problems.append(msg)


def ask(question, default=True):
    if CHECK:
        return False
    hint = "Y/n" if default else "y/N"
    while True:
        a = input(f"  ? {question} [{hint}] ").strip().lower()
        if not a:
            return default
        if a in ("y", "yes", "n", "no"):
            return a.startswith("y")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def step(n, title):
    say(f"\n{n}. {title}")


# ---------------------------------------------------------------- steps
def tools():
    step(1, "Tools")
    v = sys.version_info
    (ok if v >= (3, 11) else todo)(f"Python {v.major}.{v.minor}" + ("" if v >= (3, 11) else " - need 3.11 or newer"))
    for name, need, hint in (("git", True, "winget install Git.Git"),
                             ("node", True, "winget install OpenJS.NodeJS.LTS"),
                             ("claude", True, "irm https://claude.ai/install.ps1 | iex"),
                             ("codex", False, "optional: npm install -g @openai/codex")):
        path = shutil.which(name)
        if path:
            ok(f"{name} found")
        elif need:
            todo(f"{name} missing -> {hint}")
        else:
            say(f"  [--]   {name} not installed ({hint})")
    bun = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "bun" / "bin" / "bun.exe"
    if bun.exists():
        ok("bun found (runs the Telegram plugin)")
    elif shutil.which("npm") and ask("Bun is missing. Install it now with 'npm install -g bun'?"):
        r = subprocess.run(["npm", "install", "-g", "bun"], shell=True)
        (ok if r.returncode == 0 else todo)("bun installed" if r.returncode == 0 else "bun install failed: run npm install -g bun")
    else:
        todo("bun missing -> npm install -g bun")


def packages():
    step(2, "Python packages (yt-dlp, faster-whisper, imageio-ffmpeg)")
    missing = []
    for mod in ("yt_dlp", "faster_whisper", "imageio_ffmpeg"):
        if run([sys.executable, "-c", f"import {mod}"]).returncode != 0:
            missing.append(mod)
    if not missing:
        ok("all installed")
    elif ask(f"Missing: {', '.join(missing)}. Install from requirements.txt now?"):
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
        (ok if r.returncode == 0 else todo)("installed" if r.returncode == 0 else "pip install failed")
    else:
        todo(f"install packages: python -m pip install -r requirements.txt")


def telegram():
    step(3, "Telegram plugin and bot token")
    if not shutil.which("claude"):
        todo("install Claude Code first (step 1), then run setup again")
        return
    listing = run(["claude", "plugin", "list"]).stdout
    if PLUGIN in listing:
        ok("Telegram plugin installed")
    elif ask("Install the Telegram plugin for Claude Code now?"):
        r = run(["claude", "plugin", "install", PLUGIN])
        if r.returncode == 0:
            ok("Telegram plugin installed")
        else:
            todo(f"plugin install failed. In Claude Code run: /plugin install {PLUGIN}")
            say("        " + (r.stderr or r.stdout).strip()[:300])
    else:
        todo(f"install the plugin: claude plugin install {PLUGIN}")

    env = TG_DIR / ".env"
    has_token = env.exists() and "TELEGRAM_BOT_TOKEN=" in env.read_text(encoding="utf-8")
    if has_token:
        ok("bot token saved")
    elif not CHECK:
        say("    Paste the token BotFather gave you (it looks like 123456789:AAH...).")
        say("    It is saved only on this PC, in ~/.claude/channels/telegram/.env. Leave empty to skip.")
        token = getpass.getpass("    token (hidden): ").strip()
        if re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{20,}", token):
            TG_DIR.mkdir(parents=True, exist_ok=True)
            env.write_text(f"TELEGRAM_BOT_TOKEN={token}\n", encoding="utf-8")
            ok("bot token saved")
        else:
            todo("no valid token entered: run setup again, or /telegram:configure <token> in Claude Code")
    else:
        todo("bot token not saved yet")

    # Telegram gives each message to ONE connection: only the bot may load the plugin
    state = run(["claude", "plugin", "list"]).stdout
    block = state[state.find(PLUGIN):state.find(PLUGIN) + 200] if PLUGIN in state else ""
    if "disabled" in block:
        ok("plugin is off for other Claude sessions (the bot turns it on for itself)")
    elif PLUGIN in state:
        say("    The plugin is on for EVERY Claude session. Each one would grab the bot and swallow messages.")
        if ask("Turn it off globally? The bot switches it on for itself via .claude/bot-settings.json."):
            r = run(["claude", "plugin", "disable", PLUGIN, "-s", "user"])
            (ok if r.returncode == 0 else todo)("plugin off globally" if r.returncode == 0
                                                else f"couldn't disable: claude plugin disable {PLUGIN} -s user")
        else:
            todo(f"turn the plugin off globally: claude plugin disable {PLUGIN} -s user")


def gemini():
    step(4, "Gemini key (the main reel watcher, free)")
    env = ROOT / ".env"
    if not env.exists():
        if CHECK:
            say("  [--]   no .env yet: setup will create it from .env.example")
            return
        shutil.copy(ROOT / ".env.example", env)
    text = env.read_text(encoding="utf-8")
    if re.search(r"^GEMINI_API_KEY=\S+", text, re.M):
        ok("Gemini key saved in .env")
        return
    if CHECK:
        say("  [--]   no Gemini key: reels use the local backup watcher")
        return
    say("    Get a free key at https://aistudio.google.com/apikey -> Create API key. Leave empty to skip;")
    say("    without it the bot still works, using the slower local backup watcher.")
    key = getpass.getpass("    Gemini key (hidden): ").strip()
    if key:
        env.write_text(re.sub(r"^GEMINI_API_KEY=.*$", f"GEMINI_API_KEY={key}", text, flags=re.M), encoding="utf-8")
        ok("Gemini key saved in .env (git ignores this file)")
    else:
        say("  [--]   skipped: reels use the local backup watcher")


def reminders():
    step(5, "Reminders (one list for every Claude session, pings you on Telegram)")
    remind = ROOT / "reminders" / "remind.py"
    tasks = run(["powershell", "-NoProfile", "-Command",
                 "Get-ScheduledTask -TaskPath '\\ClaudeReminders\\' -TaskName AtLogon -ErrorAction SilentlyContinue | "
                 "ForEach-Object { $_.Actions[0].Arguments }"]).stdout
    if str(remind) in tasks:
        ok("Task Scheduler set up (one entry per reminder, no polling)")
    elif ask("Set up reminders in Task Scheduler? Nothing runs until a reminder is due."):
        r = run([sys.executable, str(remind), "install"])
        (ok if "installed" in r.stdout else todo)(r.stdout.strip().splitlines()[0] if r.stdout.strip() else "install failed")
    else:
        todo(f"set up reminders: python reminders\\remind.py install")

    gmd = HOME / ".claude" / "CLAUDE.md"
    current = gmd.read_text(encoding="utf-8") if gmd.exists() else ""
    if "remind.py" in current:
        ok("your global CLAUDE.md tells every session about the reminder list")
    elif ask("Tell every Claude session about the list (adds a short section to ~/.claude/CLAUDE.md)?"):
        snippet = (ROOT / "reminders" / "CLAUDE-snippet.md").read_text(encoding="utf-8")
        snippet = snippet.replace("{{REMIND_FWD}}", str(remind).replace("\\", "/")).replace("{{REMIND}}", str(remind))
        gmd.parent.mkdir(parents=True, exist_ok=True)
        gmd.write_text(current.rstrip() + ("\n\n" if current.strip() else "") + snippet, encoding="utf-8")
        ok("added to ~/.claude/CLAUDE.md")
    else:
        say("  [--]   not set up (optional): only the Telegram bot will use the reminder list")


def autostart():
    step(6, "Start with Windows, Sunday digest, command menu")
    lnk = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Reel agent.lnk"
    if lnk.exists():
        ok("starts at login (Startup shortcut) + Sunday digest task")
    elif ask("Start the bot automatically when you log in, and send a digest on Sundays?"):
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "install-autostart.ps1")])
        (ok if r.returncode == 0 else todo)("autostart on" if r.returncode == 0 else "autostart failed")
    else:
        say("  [--]   not set up (optional): start it yourself with .\\bot start")
    if (TG_DIR / ".env").exists():
        if CHECK:
            ok("command menu can be registered (maintain.py menu)")
        else:
            r = run([sys.executable, str(ROOT / ".claude" / "skills" / "reel-watch" / "scripts" / "maintain.py"), "menu"])
            (ok if "True" in r.stdout else todo)("Telegram '/' menu registered" if "True" in r.stdout
                                                 else "menu not registered (check the token)")


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    say("Reel Agent setup" + ("  (check only, nothing will change)" if CHECK else ""))
    say(f"Project folder: {ROOT}")
    tools()
    packages()
    telegram()
    gemini()
    reminders()
    autostart()
    say("\n" + "-" * 60)
    if problems:
        say("Still to do:")
        for p in problems:
            say(f"  - {p}")
        say("Fix these, then run setup again.")
    else:
        say("Setup complete. Next:")
        say("  1. .\\bot start           (opens the bot window; keep it open)")
        say("  2. Message your bot on Telegram: it replies with a pairing code")
        say("  3. In the bot window:    /telegram:access pair <code>")
        say("  4. Then:                 /telegram:access policy allowlist")
        say("  5. Send /menu from your phone")


if __name__ == "__main__":
    main()
