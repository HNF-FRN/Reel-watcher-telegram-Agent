"""pc_link.py - keeps the PC's reel library and reminders in step with the cloud Worker (cloud mode).

Nothing runs in the background. start.ps1 calls `sync` each time the bot starts; jobs.py and remind.py start a
`push` (as a detached process, so they never wait on the network) after each change; remind.py asks `claim`
before sending a reminder, so the PC and the cloud never both send it. Without "cloud_url" in reels/config.json
every command is a quiet no-op: the PC bot works the same without cloud mode.

    python cloud/pc_link.py sync             pull what the cloud did while the PC was off, then push
    python cloud/pc_link.py push             push changed reels and the open reminders
    python cloud/pc_link.py claim R3 "2026-09-25 09:00"   prints send or skip
    python cloud/pc_link.py cmd "/yes 12"     run a bot command in the cloud (for cloud jobs); it answers on Telegram
    python cloud/pc_link.py status           cloud or PC mode, failover, cloud tasks running
    python cloud/pc_link.py setup <worker-url>   save the Worker URL and do a first sync

The Worker checks a hash of the bot token (both sides already have the token), so there is no extra secret.
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".claude" / "skills" / "reel-watch" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from common import CONFIG, REELS, _token, read_json, utf8_stdio, write_json  # noqa: E402

SYNC_STATE = REELS / ".cloud_sync.json"
IN_CLOUD = ("queued", "watching", "planning", "building")  # cloud-side statuses of work still in progress


def cloud_url():
    return ((read_json(CONFIG, {}) or {}).get("cloud_url") or "").rstrip("/")


def configured():
    return bool(cloud_url() and _token())


def call(method, path, body=None, timeout=15):
    token = hashlib.sha256(f"reel-agent-pc:{_token()}".encode()).hexdigest()
    data = json.dumps(body).encode() if body is not None else None
    # Cloudflare rejects urllib's default "Python-urllib" User-Agent (403, error 1010).
    request = urllib.request.Request(cloud_url() + path, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "reel-agent-pc/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def tz_offset_min():
    return int(datetime.now().astimezone().utcoffset().total_seconds() // 60)


# ---------------------------------------------------------------- local modules (imported lazily: they lock files)
def jobs_module():
    import jobs  # noqa: PLC0415 - the skill's jobs.py
    return jobs


def remind_module():
    sys.path.insert(0, str(ROOT / "reminders"))
    import remind  # noqa: PLC0415
    return remind


# ---------------------------------------------------------------- push (PC -> cloud)
def job_payload(n, j):
    breakdown = ""
    bd = Path(j.get("reel_dir") or "") / "breakdown.md"
    if j.get("reel_dir") and bd.exists():
        breakdown = bd.read_text(encoding="utf-8", errors="replace")[:60000]
    return {"id": int(n), "status": j.get("status") or "done", "summary": j.get("summary") or j.get("error") or "",
            "source": j.get("source") or "", "note": j.get("note") or "", "tags": ",".join(j.get("tags") or []),
            "breakdown": breakdown, "created": j.get("created") or ""}


def digest(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def open_reminders(data):
    return [{"rid": rid, "text": v["text"], "note": v.get("note"), "source": v.get("source"), "every": v.get("every"),
             "due": v.get("due"), "status": v["status"], "sent": v.get("sent")}
            for rid, v in data["items"].items() if v["status"] == "open"]


def push(force=False):
    jobs = read_json(REELS / "jobs.json", {"next": 1, "jobs": {}}) or {"next": 1, "jobs": {}}
    remind = remind_module()
    reminders = read_json(remind.DATA, {"next": 1, "items": {}}) or {"next": 1, "items": {}}
    state = read_json(SYNC_STATE, {}) or {}
    sent = state.setdefault("jobs", {})
    changed = []
    for n, j in jobs["jobs"].items():
        payload = job_payload(n, j)
        if force or sent.get(n) != digest(payload):
            changed.append(payload)
    rem = open_reminders(reminders)
    rem_changed = force or state.get("reminders") != digest(rem)
    base = {"tz_offset_min": tz_offset_min(), "next_job": jobs["next"], "next_reminder": reminders["next"]}
    batches = [changed[i:i + 20] for i in range(0, len(changed), 20)] or [[]]
    for i, batch in enumerate(batches):
        body = {**base, "jobs": batch}
        if i == 0 and rem_changed:
            body["reminders"] = rem
        call("POST", "/pc/sync", body, timeout=30)
        for payload in batch:
            sent[str(payload["id"])] = digest(payload)
    state["reminders"] = digest(rem)
    write_json(SYNC_STATE, state)
    return len(changed), rem_changed


# ---------------------------------------------------------------- pull (cloud -> PC)
def source_text(raw):
    try:
        src = json.loads(raw)
    except (TypeError, ValueError):
        return raw or ""
    if src.get("url"):
        return src["url"]
    if src.get("idea"):
        return f"idea: {src['idea'][:100]}"
    files = src.get("files") or [src]
    return f"telegram {'photos' if all(f.get('kind') == 'photo' for f in files) else 'video'} (cloud)"


def local_time(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except (AttributeError, ValueError):
        return datetime.now().strftime("%Y-%m-%d %H:%M")


def apply_jobs(rows, state):
    if not rows:
        return
    jobs = jobs_module()
    sent = state.setdefault("jobs", {})
    with jobs.locked() as data:
        for row in rows:
            n = str(row["id"])
            j = data["jobs"].get(n)
            new = j is None
            if not new and row.get("origin") == "cloud" and not j.get("cloud"):
                continue  # same number as a different local reel (made before the PC's numbers were known)
            if new:
                j = data["jobs"][n] = {"source": source_text(row["source"]), "chat_id": str(row.get("chat_id") or ""),
                                       "note": row.get("note") or "", "created": local_time(row.get("created_at")),
                                       "cloud": True}
            status = row["status"]
            j["status"] = f"cloud-{status}" if status in IN_CLOUD else status
            if row.get("summary"):
                j["summary"] = row["summary"]
            if row.get("tags"):
                j["tags"] = [t for t in row["tags"].split(",") if t]
            if row.get("branch_url"):
                j["branch_url"] = row["branch_url"]
            if row.get("breakdown") or row.get("plan"):
                d = Path(j.get("reel_dir") or REELS / f"{datetime.now():%Y%m%d-%H%M%S}_cloud{n}")
                d.mkdir(parents=True, exist_ok=True)
                j["reel_dir"] = str(d)
                if row.get("breakdown") and j.get("cloud"):
                    (d / "breakdown.md").write_text(row["breakdown"], encoding="utf-8")
                if row.get("plan"):
                    (d / "PLAN.md").write_text(row["plan"], encoding="utf-8")
                    j["cloud_plan"] = str(d / "PLAN.md")
            if status == "done" and not j.get("finished"):
                j["finished"] = local_time(row.get("updated_at"))
                if new or j.get("cloud"):
                    rel = os.path.relpath(j.get("reel_dir") or REELS, ROOT).replace("\\", "/")
                    with open(REELS / "INDEX.md", "a", encoding="utf-8") as f:
                        f.write(f"- {datetime.now():%Y-%m-%d} | #{n} | {j.get('summary', '')} (cloud) | {rel}\n")
            data["next"] = max(data["next"], int(n) + 1)
            sent[n] = digest(job_payload(n, j))  # already in the cloud: don't push it straight back


def apply_reminders(rows):
    if not rows:
        return
    remind = remind_module()
    with remind.store() as data:
        for r in rows:
            rid = r["rid"]
            item = data["items"].get(rid)
            if r["status"] == "done":
                if item:
                    item.update(status="done", done_at=datetime.now().strftime(remind.FMT))
                continue
            fields = {"text": r["text"], "due": r.get("due_local"), "every": r.get("every"), "note": r.get("note"),
                      "source": r.get("source") or "cloud", "status": "open",
                      "sent": r["sent_due"] if r.get("sent_due") and r.get("sent_due") == r.get("due_local") else None}
            if item:
                item.update(fields)
            else:
                data["items"][rid] = {**fields, "created": datetime.now().strftime(remind.FMT)}
            data["next"] = max(data["next"], int(rid[1:]) + 1)
        remind.sync(data)


def pull():
    changes = call("GET", "/pc/changes")
    state = read_json(SYNC_STATE, {}) or {}
    apply_jobs(changes.get("jobs") or [], state)
    apply_reminders(changes.get("reminders") or [])
    write_json(SYNC_STATE, state)
    call("POST", "/pc/ack", {
        "jobs": [{"id": x["id"], "updated_at": x["updated_at"]} for x in changes.get("jobs") or []],
        "reminders": [{"rid": x["rid"], "updated_at": x["updated_at"]} for x in changes.get("reminders") or []],
    })
    return len(changes.get("jobs") or []), len(changes.get("reminders") or []), changes.get("mode")


# ---------------------------------------------------------------- helpers for jobs.py / remind.py
def push_soon():
    """Start `push` in a detached, windowless process; the caller doesn't wait for the network."""
    if not configured():
        return
    exe = Path(sys.executable)
    if os.name == "nt" and exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")
    flags = 0x00000008 | 0x08000000 if os.name == "nt" else 0  # DETACHED_PROCESS | CREATE_NO_WINDOW
    try:
        subprocess.Popen([str(exe), str(Path(__file__).resolve()), "push", "--quiet"], cwd=str(ROOT), creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    except OSError:
        pass


def claim(rid, due):
    """True if this PC should send reminder `rid` for `due` now. Sends anyway if the cloud can't be reached."""
    if not configured():
        return True
    try:
        return bool(call("POST", "/pc/reminder/claim", {"rid": rid, "due": due}, timeout=10).get("send", True))
    except Exception:
        return True


def main():
    utf8_stdio()
    args = sys.argv[1:]
    quiet = "--quiet" in args
    args = [a for a in args if a != "--quiet"]
    if not args:
        print(__doc__)
        return 2
    cmd = args[0]
    if cmd == "setup":
        if len(args) != 2 or not args[1].startswith("https://"):
            print("usage: python cloud/pc_link.py setup https://<your-worker>.workers.dev")
            return 2
        cfg = read_json(CONFIG, {}) or {}
        cfg["cloud_url"] = args[1].rstrip("/").removesuffix("/telegram")
        write_json(CONFIG, cfg)
        cmd = "sync"
    if not configured():
        if not quiet:
            print("cloud mode is not set up (no cloud_url in reels/config.json)")
        return 0
    try:
        if cmd == "sync":
            jobs_in, rem_in, mode = pull()
            jobs_out, rem_out = push(force="--force" in args)
            print(f"cloud sync: {jobs_in} reel(s) and {rem_in} reminder(s) from the cloud; "
                  f"sent {jobs_out} reel(s){' and reminders' if rem_out else ''}; mode was {mode or 'pc'}")
        elif cmd == "push":
            jobs_out, rem_out = push()
            if not quiet:
                print(f"pushed {jobs_out} reel(s){' and reminders' if rem_out else ''}")
        elif cmd == "claim" and len(args) == 3:
            print("send" if claim(args[1], args[2]) else "skip")
        elif cmd == "cmd" and len(args) == 2:
            call("POST", "/pc/command", {"text": args[1]})
            print("sent to the cloud; it answers on Telegram")
        elif cmd == "status":
            s = call("GET", "/pc/status")
            active = ", ".join(f"#{x['job_id']} {x['action']} ({x['status']})" for x in s.get("active") or []) or "none"
            print(f"mode: {s.get('mode') or 'pc'} since {s.get('since') or '-'} | failover: {s.get('failover')} | "
                  f"cloud tasks: {active}")
        else:
            print(__doc__)
            return 2
    except (urllib.error.URLError, OSError, ValueError) as exc:
        if not (quiet and cmd == "push"):  # a background push fails silently; the next sync catches up
            print(f"cloud unreachable: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
