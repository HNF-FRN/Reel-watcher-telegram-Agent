"""Shared helpers for the reel-agent scripts: paths, locked JSON files, config, Telegram, usage tracking."""
import ctypes
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[3]                      # the project folder
REELS = ROOT / "reels"
BUILDS = ROOT.parent / "builds"                # "builds" next to the project (outside it on purpose:
                                               # a build inside would inherit the bots CLAUDE.md)
CONFIG = REELS / "config.json"
GEMINI_USAGE = REELS / ".gemini_usage.json"
CLAUDE_USAGE = REELS / ".claude_usage.json"
TG_DIR = Path.home() / ".claude" / "channels" / "telegram"

CLAUDE_MODELS = ("haiku", "sonnet", "opus", "fable")
BUILD_MODELS = CLAUDE_MODELS + ("codex",)
TASKS = ("watch", "research", "plan", "build")
MODES = ("safe", "normal")  # shell commands always need the user's OK (or an /always rule)
DEFAULTS = {
    "models": {"watch": "sonnet", "research": "haiku", "plan": "opus", "build": "sonnet"},
    "mode": "normal",            # default permission level for builds
    "limit_min": 60,             # active minutes before a build is stopped (waiting for you doesn't count)
    "approval_timeout_min": 30,  # unanswered approval requests are denied after this
    "budget_usd": None,          # optional --max-budget-usd per build run
}


def utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    for _ in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError:  # Windows: reader has it open for a moment
            time.sleep(0.05)
    tmp.unlink(missing_ok=True)


@contextmanager
def file_lock(path, stale_sec=30):
    lock = Path(str(path) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    for _ in range(400):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale_sec:
                    lock.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
            lock.unlink(missing_ok=True)


def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    saved = read_json(CONFIG, {}) or {}
    cfg["models"].update(saved.get("models") or {})
    for k in DEFAULTS:
        if k != "models" and k in saved:
            cfg[k] = saved[k]
    return cfg


def save_config(cfg):
    write_json(CONFIG, cfg)


# Variables that tie a process to the Claude session that launched it. A build (or the bot) that inherits them
# runs as that session's "child": no saved transcript (so no --resume) and odd routing. Drop them for children.
SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID",
                "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_PID", "CLAUDE_CODE_ENTRYPOINT",
                "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SSE_PORT")


def clean_env():
    return {k: v for k, v in os.environ.items() if k.upper() not in SESSION_VARS}


def pid_alive(pid):
    """Windows-safe (os.kill(pid, 0) would send CTRL_C on Windows)."""
    if not pid:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


# ---------------------------------------------------------------- Telegram (outbound only)
def _token():
    env = TG_DIR / ".env"
    for row in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        k, _, v = row.partition("=")
        if k.strip() == "TELEGRAM_BOT_TOKEN":
            return v.strip().strip('"')
    return None


def tg_chats():
    return (read_json(TG_DIR / "access.json", {}) or {}).get("allowFrom", [])


def tg_send(text, chat_id=None):
    """Send a plain-text message to the user (their own DM). Returns True if it went out."""
    if os.environ.get("REEL_TG_DRYRUN"):  # tests: log instead of messaging the phone
        with open(REELS / ".tg_dryrun.log", "a", encoding="utf-8") as f:
            f.write(f"--- {now()} to {chat_id or 'owner'}\n{text}\n")
        return True
    token = _token()
    chats = [chat_id] if chat_id else tg_chats()
    if not token or not chats:
        return False
    ok = True
    for chat in chats:
        for i in range(0, len(text), 4000):  # Telegram caps messages at 4096 chars
            body = urllib.parse.urlencode({"chat_id": chat, "text": text[i:i + 4000],
                                           "disable_web_page_preview": "true"}).encode()
            try:
                urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", body, timeout=30).read()
            except Exception as e:
                print(f"telegram send failed: {e}", file=sys.stderr)
                ok = False
    return ok


def tg_api(method, params):
    token = _token()
    body = json.dumps(params).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", body,
                                 {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))


# ---------------------------------------------------------------- usage tracking
def quota_day():
    """Gemini free-tier quotas reset at midnight Pacific time."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:
        from datetime import timedelta, timezone
        return (datetime.now(timezone.utc) - timedelta(hours=8)).strftime("%Y-%m-%d")


def seconds_to_pacific_midnight():
    try:
        from zoneinfo import ZoneInfo
        t = datetime.now(ZoneInfo("America/Los_Angeles"))
    except Exception:
        from datetime import timedelta, timezone
        t = datetime.now(timezone.utc) - timedelta(hours=8)
    return int(86400 - (t.hour * 3600 + t.minute * 60 + t.second))


def gemini_usage_update(fn):
    """fn(day_record) mutates today's record: {"models": {m: {...}}, "exhausted": [...], "limits": {...}}."""
    with file_lock(GEMINI_USAGE):
        data = read_json(GEMINI_USAGE, {}) or {}
        if data.get("day") != quota_day():
            data = {"day": quota_day(), "models": {}, "exhausted": [], "limits": data.get("limits", {})}
        fn(data)
        write_json(GEMINI_USAGE, data)


def gemini_usage():
    data = read_json(GEMINI_USAGE, {}) or {}
    if data.get("day") != quota_day():
        return {"day": quota_day(), "models": {}, "exhausted": [], "limits": data.get("limits", {})}
    return data


def claude_usage_record(info):
    """Store a rate_limit_event's info from a headless Claude run (per limit type)."""
    if not isinstance(info, dict):
        return
    with file_lock(CLAUDE_USAGE):
        data = read_json(CLAUDE_USAGE, {}) or {}
        kind = info.get("rateLimitType") or "unknown"
        data[kind] = {**info, "seen": now()}
        write_json(CLAUDE_USAGE, data)
