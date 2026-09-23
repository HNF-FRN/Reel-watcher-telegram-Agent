"""maintain.py - housekeeping for the reel agent. Runs without Claude (start.ps1 and Task Scheduler call it).

Usage:
    python maintain.py startup              after a (re)start: reel jobs and builds left running are marked
                                            interrupted, bot start time is recorded, old media cleaned up
    python maintain.py cleanup [--days 30]  delete downloaded videos/audio older than N days (keeps breakdowns,
                                            frames, images and manifests)
    python maintain.py digest [--send]      weekly summary of saved / unanswered reels and builds; --send posts it
    python maintain.py menu                 register the "/" command menu with Telegram (run once; safe to repeat)
    python maintain.py alert "text"         send a plain message to the user (start.ps1 uses it when the bot can't start)

Telegram messages go to the chat IDs in ~/.claude/channels/telegram/access.json (allowFrom): the user's own DM.
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BUILDS, REELS, SCRIPTS, now, read_json, tg_api, tg_send, utf8_stdio, write_json  # noqa: E402

JOBS = REELS / "jobs.json"

# Shown when the user types "/" in the chat. /start /help /status belong to the Telegram plugin itself.
MENU = [
    ("menu", "All commands, short"),
    ("jobs", "Recent reels and what's running"),
    ("tasks", "Everything running right now"),
    ("pending", "Everything waiting for you"),
    ("plan", "N [model] – plan a build, no changes"),
    ("build", "N [model] [safe|normal] – build it"),
    ("tell", "N message – steer a build"),
    ("yes", "N – approve what build N asked"),
    ("no", "N [reason] – deny it"),
    ("always", "N – approve and allow that command for this build"),
    ("peek", "N – what build N is doing now"),
    ("stop", "N – stop build N"),
    ("resume", "N [message] – continue build N"),
    ("diff", "N – what build N changed"),
    ("undo", "N – throw away build N's changes"),
    ("deploy", "N – install a finished build"),
    ("log", "N – full build log as a file"),
    ("remind", "when what – e.g. /remind tomorrow 9:00 call the bank"),
    ("todo", "what – add a to-do (alone: show the list)"),
    ("reminders", "Your reminders and to-dos"),
    ("done", "R3 – mark a reminder done"),
    ("snooze", "R3 1h – push a reminder back"),
    ("quota", "Gemini + Claude usage left"),
    ("models", "Which model does what"),
    ("model", "task model – e.g. /model build opus"),
    ("mode", "safe|normal – default build mode"),
    ("limit", "minutes – default build time limit"),
    ("pc", "PC health"),
    ("r", "N [full] – resend a breakdown"),
    ("find", "words – search your reels"),
    ("saved", "Reels you saved for later"),
    ("new", "idea – plan/build something without a reel"),
    ("retry", "N – watch a reel again"),
    ("rewatch", "N [deep|local] – re-watch more carefully"),
    ("digest", "Weekly summary now"),
    ("manual", "Send the full manual"),
]


def cleanup(days):
    cutoff = time.time() - days * 86400
    freed, n = 0, 0
    for d in REELS.iterdir() if REELS.exists() else []:
        if not d.is_dir() or d.stat().st_mtime > cutoff:
            continue
        for f in list(d.glob("video.*")) + list(d.glob("audio.wav")):
            freed += f.stat().st_size
            f.unlink()
            n += 1
    print(f"cleanup: removed {n} media files older than {days} days, freed {freed / 1e6:.1f} MB")


def startup():
    for script, arg in (("jobs.py", "interrupted"), ("build.py", "recover")):
        r = subprocess.run([sys.executable, str(SCRIPTS / script), arg], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
    for tmp in REELS.glob("*.tmp"):
        tmp.unlink(missing_ok=True)
    for lock in list(REELS.glob("*.lock")) + [REELS / ".jobs.lock"]:
        lock.unlink(missing_ok=True)  # no worker can be alive before the bot starts
    info = read_json(REELS / ".bot_started.json", {}) or {}
    write_json(REELS / ".bot_started.json", {"at": now(), "count": info.get("count", 0) + 1})
    cleanup(30)


def build_digest():
    jobs = (read_json(JOBS, {}) or {}).get("jobs", {})
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    recent = [j for j in jobs.values() if j["created"][:10] >= week_ago]

    def rows(status, limit=10):
        items = sorted(((int(n), j) for n, j in jobs.items() if j["status"] == status), reverse=True)
        return [f"• #{n} {(j.get('summary') or j['source'])[:80]}" for n, j in items[:limit]]

    builds = []
    for d in sorted(BUILDS.glob("*-*")) if BUILDS.exists() else []:
        st = read_json(d / ".reel" / "state.json", {}) or {}
        if st.get("status") in ("plan-ready", "question", "interrupted", "stopped", "done") and not st.get("deployed"):
            builds.append(f"• #{st.get('job')} {st.get('kind')} {st.get('status')} ({st.get('model')})")

    saved, waiting, broken = rows("saved"), rows("done"), rows("interrupted") + rows("failed", 5)
    parts = [f"📬 Weekly reel digest: {len(recent)} reel{'s' if len(recent) != 1 else ''} this week"]
    if saved:
        parts.append("Saved, not built yet:\n" + "\n".join(saved))
    if waiting:
        parts.append("Watched, waiting for your answer:\n" + "\n".join(waiting))
    if builds:
        parts.append("Builds to look at:\n" + "\n".join(builds[:10]))
    if broken:
        parts.append("Didn't finish (/retry N):\n" + "\n".join(broken))
    if not (saved or waiting or broken or builds):
        parts.append("Nothing waiting on you. 🎉")
    else:
        parts.append("e.g. /plan 4 · /build 4 sonnet · /pending")
    return "\n\n".join(parts)


def main():
    utf8_stdio()
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("startup")
    sub.add_parser("menu")
    p = sub.add_parser("cleanup"); p.add_argument("--days", type=int, default=30)
    p = sub.add_parser("digest"); p.add_argument("--send", action="store_true")
    p = sub.add_parser("alert"); p.add_argument("text")
    a = ap.parse_args()
    if a.cmd == "startup":
        startup()
    elif a.cmd == "cleanup":
        cleanup(a.days)
    elif a.cmd == "menu":
        r = tg_api("setMyCommands", {"commands": [{"command": c, "description": d[:256]} for c, d in MENU]})
        print(f"menu registered: {r.get('ok')} ({len(MENU)} commands)")
    elif a.cmd == "alert":
        print("sent" if tg_send(a.text) else "send failed")
    elif a.cmd == "digest":
        text = build_digest()
        print(text)
        if a.send:
            print("sent" if tg_send(text) else "send failed")


if __name__ == "__main__":
    main()
