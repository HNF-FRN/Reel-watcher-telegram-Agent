"""remind.py - one to-do / reminder list shared by every Claude session and the Telegram bot.

Reminders live in this folder (reminders.json, mirrored to REMINDERS.md for reading), so they survive the
session that created them. Nothing polls: every upcoming reminder gets its own one-time Windows Task Scheduler
entry (Task Scheduler folder "ClaudeReminders") that fires once at that minute, sends it to the owner's Telegram through the
Reel Agent bot, and is cleaned up afterwards. "Run as soon as possible if missed" plus one check at logon catch
reminders that came due while the PC was off.

    python remind.py add "text" [--at "2026-09-23 09:00" | --at "tomorrow 9:00" | --in 2h] [--every day|weekday|week|month]
                     [--note "details"] [--source "which session / project"]
        (no --at/--in = a plain to-do with no ping)
    python remind.py list [--all]                 open items, soonest first
    python remind.py done R3                      mark done (a repeating reminder moves to its next time instead)
    python remind.py snooze R3 1h|30m|2d|"tomorrow 9:00"
    python remind.py delete R3
    python remind.py due                          what's due now (for session-start checks)
    python remind.py send-due                     what the scheduled entries run
    python remind.py sync                         rebuild the Task Scheduler entries from the list
    python remind.py install | uninstall          set up / remove everything in Task Scheduler
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "reminders.json"
VIEW = HERE / "REMINDERS.md"
LOCK = HERE / ".reminders.lock"
TG_DIR = Path.home() / ".claude" / "channels" / "telegram"
TASK_PATH = "\\ClaudeReminders\\"
OLD_POLL_TASK = "ClaudeReminders"  # the 5-minute poller this replaced
NO_WINDOW = 0x08000000
FMT = "%Y-%m-%d %H:%M"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


# ---------------------------------------------------------------- storage
@contextmanager
def store(write=True):
    for _ in range(400):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - LOCK.stat().st_mtime > 30:
                    LOCK.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            time.sleep(0.05)
    else:
        sys.exit("reminders are locked by another process; try again")
    try:
        data = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {"next": 1, "items": {}}
        yield data
        if write:
            tmp = DATA.with_name(f"reminders.{uuid.uuid4().hex[:6]}.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(DATA)
            write_view(data)
    finally:
        os.close(fd)
        LOCK.unlink(missing_ok=True)


def write_view(data):
    rows = ["# Reminders and to-dos", "",
            "Generated from reminders.json — change it with `python remind.py …` or from Telegram (/remind, /todo, /done).", ""]
    open_items = sorted(((k, v) for k, v in data["items"].items() if v["status"] == "open"), key=sort_key)
    rows.append("## Open")
    rows += [f"- **{k}** {when_txt(v)} — {v['text']}" + (f"  \n  {v['note']}" if v.get("note") else "") for k, v in open_items] or ["- (nothing)"]
    done = sorted(((k, v) for k, v in data["items"].items() if v["status"] == "done"), key=lambda kv: kv[1].get("done_at", ""), reverse=True)[:20]
    rows += ["", "## Recently done"] + ([f"- ~~{k} {v['text']}~~ ({v.get('done_at', '')})" for k, v in done] or ["- (nothing)"])
    VIEW.write_text("\n".join(rows) + "\n", encoding="utf-8")


def sort_key(kv):
    return kv[1].get("due") or "9999"


def when_txt(v):
    if not v.get("due"):
        return "(to-do)"
    rep = f", every {v['every']}" if v.get("every") else ""
    return f"({v['due']}{rep})"


# ---------------------------------------------------------------- time parsing
def parse_when(text, now=None):
    now = now or datetime.now()
    t = text.strip().lower()
    m = re.fullmatch(r"(?:in\s+)?(\d+)\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?|w|weeks?)", t)
    if m:
        n, u = int(m.group(1)), m.group(2)[0]
        return now + {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[u]
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            d = datetime.strptime(text.strip(), fmt)
            return d.replace(hour=9) if fmt == "%Y-%m-%d" else d
        except ValueError:
            pass
    m = re.fullmatch(r"(today|tonight|tomorrow|" + "|".join(WEEKDAYS) + r")(?:\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?", t)
    if m:
        day, hh, mm, ap = m.groups()
        base = now.date()
        if day == "tomorrow":
            base += timedelta(days=1)
        elif day in WEEKDAYS:
            ahead = (WEEKDAYS.index(day) - now.weekday()) % 7 or 7
            base += timedelta(days=ahead)
        hour = int(hh) if hh else (20 if day == "tonight" else 9)
        if ap == "pm" and hour < 12:
            hour += 12
        if ap == "am" and hour == 12:
            hour = 0
        return datetime.combine(base, datetime.min.time()).replace(hour=hour, minute=int(mm or 0))
    m = re.fullmatch(r"(?:at\s+)?(\d{1,2}):(\d{2})", t)
    if m:
        d = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        return d if d > now else d + timedelta(days=1)
    sys.exit(f"Can't read the time '{text}'. Use e.g. 2026-09-23 09:00, tomorrow 9:00, friday 18:00, 2h, 30m, 3d.")


def next_time(due, every):
    d = datetime.strptime(due, FMT)
    now = datetime.now()
    while d <= now:
        if every == "day":
            d += timedelta(days=1)
        elif every == "weekday":
            d += timedelta(days=1)
            while d.weekday() >= 5:
                d += timedelta(days=1)
        elif every == "week":
            d += timedelta(weeks=1)
        elif every == "month":
            d = d.replace(year=d.year + (d.month // 12), month=d.month % 12 + 1)
        else:
            return None
    return d.strftime(FMT)


# ---------------------------------------------------------------- telegram
def tg_send(text):
    if os.environ.get("REMIND_DRYRUN") or (HERE / ".dryrun").exists():  # tests: log instead of pinging the phone
        with open(HERE / "dryrun.log", "a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now():%H:%M:%S}\n{text}\n")
        return True
    token = None
    env = TG_DIR / ".env"
    for row in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        k, _, v = row.partition("=")
        if k.strip() == "TELEGRAM_BOT_TOKEN":
            token = v.strip().strip('"')
    try:
        chats = json.loads((TG_DIR / "access.json").read_text(encoding="utf-8")).get("allowFrom", [])
    except Exception:
        chats = []
    if not token or not chats:
        return False
    for chat in chats:
        body = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", body, timeout=30).read()
        except Exception as e:
            print(f"telegram send failed: {e}", file=sys.stderr)
            return False
    return True


# ---------------------------------------------------------------- Task Scheduler (no polling)
def run_ps(script):
    """Run a PowerShell script without flashing a window (safe under pythonw)."""
    import base64
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                          capture_output=True, text=True, creationflags=NO_WINDOW)


def runner_cmd():
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return str(pyw if pyw.exists() else Path(sys.executable)), f'"{Path(__file__).resolve()}" send-due'


def sync(data, retry_minutes=2):
    """Make Task Scheduler hold exactly one one-time entry per upcoming reminder."""
    now = datetime.now()
    desired = []
    for rid, v in data["items"].items():
        if v["status"] != "open" or not v.get("due") or v.get("sent") == v["due"]:
            continue
        at = datetime.strptime(v["due"], FMT)
        if at <= now:  # overdue but not sent yet (PC was off, or sending failed): try again shortly
            at = now + timedelta(minutes=retry_minutes)
        desired.append({"name": f"Reminder-{rid}", "at": at.strftime(FMT)})
    exe, args = runner_cmd()
    script = f"""
$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'
$path = '{TASK_PATH}'
$json = @'
{json.dumps(desired)}
'@
# Windows PowerShell 5 returns a JSON array as one object: unroll it
$desired = @((ConvertFrom-Json $json) | ForEach-Object {{ $_ }})
$existing = @(Get-ScheduledTask -TaskPath $path -ErrorAction SilentlyContinue | Where-Object {{ $_.TaskName -like 'Reminder-R*' }})
$now = Get-Date
foreach ($t in $existing) {{
    if ($desired.name -contains $t.TaskName) {{ continue }}
    $start = [datetime]$t.Triggers[0].StartBoundary
    # leave an entry that is firing right now alone; it is inert afterwards and removed next time
    if ($start -gt $now -or $start -lt $now.AddMinutes(-10)) {{
        Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath $path -Confirm:$false
    }}
}}
$action = New-ScheduledTaskAction -Execute '{exe}' -Argument '{args}'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
foreach ($d in $desired) {{
    $at = [datetime]::ParseExact($d.at, 'yyyy-MM-dd HH:mm', [Globalization.CultureInfo]::InvariantCulture)
    $cur = $existing | Where-Object {{ $_.TaskName -eq $d.name }}
    if ($cur -and ([datetime]$cur.Triggers[0].StartBoundary) -eq $at) {{ continue }}
    Register-ScheduledTask -TaskName $d.name -TaskPath $path -Action $action -Trigger (New-ScheduledTaskTrigger -Once -At $at) -Settings $settings -Description 'Claude reminder: sends one due reminder to Telegram, then is removed.' -Force | Out-Null
}}
"""
    r = run_ps(script)
    if r.returncode != 0:
        print(f"(couldn't update Task Scheduler: {(r.stderr or r.stdout).strip()[:300]})", file=sys.stderr)
    return len(desired)


def install():
    exe, args = runner_cmd()
    r = run_ps(f"""
$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'
# the retired 5-minute poller was a task named ClaudeReminders in the root folder (not the folder itself)
Get-ScheduledTask -TaskName '{OLD_POLL_TASK}' -TaskPath '\\' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
# drop old entries so every one is re-created pointing at this copy of remind.py
Get-ScheduledTask -TaskPath '{TASK_PATH}' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
$action = New-ScheduledTaskAction -Execute '{exe}' -Argument '{args}'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'AtLogon' -TaskPath '{TASK_PATH}' -Action $action -Trigger $trigger -Settings $settings -Description 'Claude reminders: once at logon, send anything that came due while the PC was off.' -Force | Out-Null
""")
    print("installed: one-time entry per reminder + one check at logon (no polling)" if r.returncode == 0
          else f"install failed: {r.stderr.strip()[:300]}")


def uninstall():
    run_ps(f"""
Get-ScheduledTask -TaskName '{OLD_POLL_TASK}' -TaskPath '\\' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
Get-ScheduledTask -TaskPath '{TASK_PATH}' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
""")
    print("removed all Claude reminder entries from Task Scheduler (the list itself is kept)")


# ---------------------------------------------------------------- commands
def find(data, rid):
    rid = rid.upper() if rid.upper().startswith("R") else f"R{rid}"
    if rid not in data["items"]:
        sys.exit(f"No reminder {rid}. See: python remind.py list")
    return rid, data["items"][rid]


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add"); p.add_argument("text"); p.add_argument("--at"); p.add_argument("--in", dest="in_")
    p.add_argument("--every", choices=["day", "weekday", "week", "month"]); p.add_argument("--note"); p.add_argument("--source")
    p = sub.add_parser("list"); p.add_argument("--all", action="store_true")
    for name in ("done", "delete"):
        sub.add_parser(name).add_argument("id")
    p = sub.add_parser("snooze"); p.add_argument("id"); p.add_argument("when", nargs="+")
    for name in ("due", "send-due", "sync", "install", "uninstall"):
        sub.add_parser(name)
    a = ap.parse_args()

    if a.cmd == "install":
        install()
        with store(write=False) as data:
            print(f"scheduled {sync(data)} upcoming reminder(s)")
        return
    if a.cmd == "uninstall":
        uninstall()
        return

    with store(write=a.cmd not in ("list", "due")) as data:
        items = data["items"]
        if a.cmd == "add":
            when = parse_when(a.at) if a.at else parse_when(a.in_) if a.in_ else None
            if a.every and not when:
                sys.exit("--every needs a first time: add --at or --in")
            rid = f"R{data['next']}"
            data["next"] += 1
            items[rid] = {"text": a.text.strip(), "due": when.strftime(FMT) if when else None, "every": a.every,
                          "note": a.note, "source": a.source, "status": "open", "created": datetime.now().strftime(FMT),
                          "sent": None}
            print(f"OK {rid} {when_txt(items[rid])} {a.text.strip()}")
        elif a.cmd == "list":
            rows = sorted(((k, v) for k, v in items.items() if a.all or v["status"] == "open"), key=sort_key)
            if not rows:
                print("Nothing on the list.")
            now = datetime.now().strftime(FMT)
            for k, v in rows:
                flag = " ⏰ due" if v["status"] == "open" and v.get("due") and v["due"] <= now else ""
                done = " ✓" if v["status"] == "done" else ""
                print(f"{k} {when_txt(v)}{flag}{done} {v['text']}" + (f" [{v['source']}]" if v.get("source") else ""))
        elif a.cmd == "done":
            rid, v = find(data, a.id)
            nxt = None
            if v.get("every") and v.get("due"):
                # a repeating reminder already moved on when it was sent: keep that next time
                nxt = v["due"] if v["due"] > datetime.now().strftime(FMT) else next_time(v["due"], v["every"])
            if nxt:
                v.update(due=nxt, sent=None)
                print(f"OK {rid} done for now; next time {nxt}")
            else:
                v.update(status="done", done_at=datetime.now().strftime(FMT))
                print(f"OK {rid} done: {v['text']}")
        elif a.cmd == "delete":
            rid, v = find(data, a.id)
            del items[rid]
            print(f"OK {rid} deleted")
        elif a.cmd == "snooze":
            rid, v = find(data, a.id)
            when = parse_when(" ".join(a.when))
            v.update(due=when.strftime(FMT), sent=None, status="open")
            print(f"OK {rid} snoozed to {v['due']}")
        elif a.cmd == "due":
            now = datetime.now().strftime(FMT)
            rows = [(k, v) for k, v in sorted(items.items(), key=sort_key) if v["status"] == "open" and v.get("due") and v["due"] <= now]
            for k, v in rows:
                print(f"⏰ {k} (due {v['due']}) {v['text']}")
        elif a.cmd == "send-due":
            now = datetime.now()
            for k, v in sorted(items.items(), key=sort_key):
                if v["status"] != "open" or not v.get("due") or v.get("sent") == v["due"]:
                    continue
                due = datetime.strptime(v["due"], FMT)
                if due > now:
                    continue
                late = f" (was due {v['due']})" if now - due > timedelta(minutes=15) else ""
                msg = f"⏰ Reminder {k}{late}\n{v['text']}"
                if v.get("note"):
                    msg += f"\n\n{v['note']}"
                if v.get("source"):
                    msg += f"\n(from {v['source']})"
                msg += f"\n\n/done {k} · /snooze {k} 1h · /snooze {k} tomorrow 9:00"
                if tg_send(msg):
                    v["sent"] = v["due"]
                    if v.get("every"):  # repeating: schedule the next one right away
                        v.update(due=next_time(v["due"], v["every"]), sent=None)
        elif a.cmd == "sync":
            print(f"scheduled {sync(data)} upcoming reminder(s)")
        # keep Task Scheduler in step with the list after every change (a failed send retries in 10 min)
        if a.cmd in ("add", "done", "delete", "snooze", "send-due"):
            sync(data, retry_minutes=10 if a.cmd == "send-due" else 2)


if __name__ == "__main__":
    main()
