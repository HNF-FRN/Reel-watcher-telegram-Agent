"""jobs.py - numbered reel jobs, so several reels can be processed at once and referred to as #N.

Usage:
    python jobs.py new --source <url-or-attachment> [--chat ID] [--msg ID] [--note TEXT]
        -> prints "JOB <n>", or "DUPLICATE <n> <status> <reel_dir>" if that link was already done/running
    python jobs.py done <n> --dir <REEL_DIR> --summary "<one line>" [--tags "mcp,skill"] [--engine E] [--reply-msg ID]
        -> marks the job done and appends it to reels/INDEX.md
    python jobs.py fail <n> --error "<why>"
    python jobs.py set <n> --status <status>        (saved, installed, dismissed, running, ...)
    python jobs.py tag <n> --tags "a,b"              (adds tags)
    python jobs.py show <n>
    python jobs.py list [--limit 15] [--status saved]
    python jobs.py search <words...>                 (summary, tags, source, breakdown.md)
    python jobs.py interrupted                       (after a restart: running jobs -> interrupted)
    python jobs.py idea "<text>" [--chat ID]         an idea typed on Telegram (no reel): a job you can /plan and /build

State lives in reels/jobs.json. Every write takes a lock file, so parallel workers are safe.
"""
import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

REELS = Path(__file__).resolve().parents[4] / "reels"
JOBS = REELS / "jobs.json"
INDEX = REELS / "INDEX.md"
LOCK = REELS / ".jobs.lock"


@contextmanager
def locked(write=True):
    REELS.mkdir(exist_ok=True)
    for _ in range(400):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - LOCK.stat().st_mtime > 30:  # stale lock from a crashed run
                    LOCK.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            time.sleep(0.05)
    else:
        sys.exit("could not lock reels/jobs.json")
    try:
        data = json.loads(JOBS.read_text(encoding="utf-8")) if JOBS.exists() else {"next": 1, "jobs": {}}
        yield data
        if write:
            tmp = JOBS.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(JOBS)
    finally:
        os.close(fd)
        LOCK.unlink(missing_ok=True)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def get(data, n):
    job = data["jobs"].get(str(n))
    if not job:
        sys.exit(f"no job #{n}")
    return job


def norm(src):
    return src.split("?")[0].rstrip("/").lower()


def tags_of(s):
    return [t.strip().lower().lstrip("#") for t in (s or "").split(",") if t.strip()]


def line(n, j):
    what = j.get("summary") or j.get("error") or j["source"]
    tags = f" [{', '.join(j['tags'])}]" if j.get("tags") else ""
    return f"#{n} ({j['status']}) {j['created'][5:]} | {what[:90]}{tags}"


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new"); p.add_argument("--source", required=True)
    p.add_argument("--chat"); p.add_argument("--msg"); p.add_argument("--note")
    p = sub.add_parser("done"); p.add_argument("n", type=int); p.add_argument("--dir", required=True)
    p.add_argument("--summary", required=True); p.add_argument("--tags"); p.add_argument("--engine")
    p.add_argument("--reply-msg")
    p = sub.add_parser("fail"); p.add_argument("n", type=int); p.add_argument("--error", required=True)
    p = sub.add_parser("set"); p.add_argument("n", type=int); p.add_argument("--status", required=True)
    p = sub.add_parser("tag"); p.add_argument("n", type=int); p.add_argument("--tags", required=True)
    p = sub.add_parser("show"); p.add_argument("n", type=int)
    p = sub.add_parser("list"); p.add_argument("--limit", type=int, default=15); p.add_argument("--status")
    p = sub.add_parser("search"); p.add_argument("words", nargs="+")
    sub.add_parser("interrupted")
    p = sub.add_parser("idea"); p.add_argument("text"); p.add_argument("--chat")
    a = ap.parse_args()

    with locked(write=a.cmd not in ("show", "list", "search")) as data:
        jobs = data["jobs"]
        if a.cmd == "new":
            src = a.source.strip()
            if src.startswith("http"):
                for n, j in jobs.items():
                    if norm(j["source"]) == norm(src) and j["status"] not in ("failed", "interrupted"):
                        print(f"DUPLICATE {n} {j['status']} {j.get('reel_dir') or '-'}")
                        return
            n = data["next"]
            data["next"] = n + 1
            jobs[str(n)] = {"source": src, "chat_id": a.chat, "msg_id": a.msg, "note": a.note,
                            "status": "running", "created": now()}
            running = sum(1 for j in jobs.values() if j["status"] == "running")
            print(f"JOB {n} (running now: {running})")
        elif a.cmd == "done":
            j = get(data, a.n)
            tags = tags_of(a.tags)
            j.update(status="done", reel_dir=a.dir, summary=a.summary, tags=tags, engine=a.engine,
                     finished=now(), reply_msg=a.reply_msg)
            rel = os.path.relpath(a.dir, REELS.parent).replace("\\", "/")
            if not INDEX.exists():
                INDEX.write_text("# Reels index\n", encoding="utf-8")
            tag_txt = " ".join(f"#{t}" for t in tags)
            with open(INDEX, "a", encoding="utf-8") as f:
                f.write(f"- {datetime.now():%Y-%m-%d} | #{a.n} | {a.summary}{' | ' + tag_txt if tag_txt else ''} | {rel}\n")
            print(f"OK #{a.n} done")
        elif a.cmd == "fail":
            get(data, a.n).update(status="failed", error=a.error, finished=now())
            print(f"OK #{a.n} failed")
        elif a.cmd == "set":
            get(data, a.n).update(status=a.status, updated=now())
            print(f"OK #{a.n} {a.status}")
        elif a.cmd == "tag":
            j = get(data, a.n)
            j["tags"] = sorted(set(j.get("tags") or []) | set(tags_of(a.tags)))
            print(f"OK #{a.n} tags: {', '.join(j['tags'])}")
        elif a.cmd == "show":
            j = get(data, a.n)
            print(json.dumps({"id": a.n, **j}, indent=2, ensure_ascii=False))
            bd = Path(j.get("reel_dir") or "") / "breakdown.md"
            if j.get("reel_dir") and bd.exists():
                print(f"BREAKDOWN: {bd}")
        elif a.cmd == "list":
            items = sorted(jobs.items(), key=lambda kv: int(kv[0]), reverse=True)
            if a.status:
                items = [kv for kv in items if kv[1]["status"] == a.status]
            if not items:
                print("(no jobs)")
            for n, j in items[:a.limit]:
                print(line(n, j))
        elif a.cmd == "search":
            words = [w.lower().lstrip("#") for w in a.words]
            hits = []
            for n, j in sorted(jobs.items(), key=lambda kv: int(kv[0]), reverse=True):
                hay = " ".join([j.get("summary") or "", " ".join(j.get("tags") or []), j["source"], j.get("note") or ""])
                bd = Path(j.get("reel_dir") or "") / "breakdown.md"
                if j.get("reel_dir") and bd.exists():
                    hay += " " + bd.read_text(encoding="utf-8", errors="replace")
                hay = hay.lower()
                if all(w in hay for w in words):
                    hits.append(line(n, j))
            # reels processed before job numbers existed only live in INDEX.md
            if INDEX.exists():
                for row in INDEX.read_text(encoding="utf-8").splitlines():
                    if row.startswith("- ") and " | #" not in row and all(w in row.lower() for w in words):
                        hits.append("(old) " + row[2:])
            print("\n".join(hits) if hits else "(nothing found)")
        elif a.cmd == "idea":
            n = data["next"]
            data["next"] = n + 1
            d = REELS / f"{datetime.now():%Y%m%d-%H%M%S}_idea{n}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "breakdown.md").write_text(f"# Idea #{n} (typed by the user)\n\n{a.text}\n", encoding="utf-8")
            summary = a.text.strip().splitlines()[0][:100]
            jobs[str(n)] = {"source": f"idea: {summary}", "chat_id": a.chat, "note": a.text, "status": "done",
                            "created": now(), "finished": now(), "reel_dir": str(d), "summary": summary,
                            "tags": ["idea"]}
            with open(INDEX, "a", encoding="utf-8") as f:
                f.write(f"- {datetime.now():%Y-%m-%d} | #{n} | idea: {summary} | #idea | "
                        f"{os.path.relpath(d, REELS.parent).replace(chr(92), '/')}\n")
            print(f"JOB {n} (idea)")
        elif a.cmd == "interrupted":
            stuck = [n for n, j in jobs.items() if j["status"] == "running"]
            for n in stuck:
                jobs[n].update(status="interrupted", finished=now())
            print(f"interrupted: {', '.join('#' + n for n in stuck) if stuck else 'none'}")


if __name__ == "__main__":
    main()
