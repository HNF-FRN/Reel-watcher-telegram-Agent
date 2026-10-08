"""Shared helpers for the reel-agent scripts: paths, locked JSON files, config, Telegram, usage tracking."""
import ctypes
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[3]                      # the project folder
# REEL_HOME keeps the library and the builds somewhere else (a server, a test). By default reels/ is in the
# project and builds/ next to it: outside it on purpose, a build inside would inherit the bot's CLAUDE.md.
HOME_DIR = Path(os.environ["REEL_HOME"]).expanduser().resolve() if os.environ.get("REEL_HOME") else None
REELS = (HOME_DIR or ROOT) / "reels"
BUILDS = HOME_DIR / "builds" if HOME_DIR else ROOT.parent / "builds"
CONFIG = REELS / "config.json"
GEMINI_USAGE = REELS / ".gemini_usage.json"
CLAUDE_USAGE = REELS / ".claude_usage.json"
TG_DIR = Path(os.environ.get("TELEGRAM_STATE_DIR") or Path.home() / ".claude" / "channels" / "telegram")

CLAUDE_MODELS = ("haiku", "sonnet", "opus", "fable")
BUILD_MODELS = CLAUDE_MODELS + ("codex", "local")  # local = agent.py with an open model (llm.py)
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW; Popen refuses creationflags elsewhere
TASKS = ("watch", "research", "plan", "build")
MODES = ("safe", "normal")  # shell commands always need the user's OK (or an /always rule)
GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash"]
GIT_ID = ["-c", "user.name=Reel agent", "-c", "user.email=reel-agent@localhost"]
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
    """<path>.lock held for the block. The lock holds its owner's "pid:token": it is broken only when that process
    has died (a live holder may take long, e.g. remind.py waiting on Task Scheduler), and a holder removes it only
    while it is still its own. A lock with no readable owner counts as stale after stale_sec."""
    lock = Path(str(path) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    me = f"{os.getpid()}:{uuid.uuid4().hex}"
    deadline = time.time() + stale_sec + 5
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, me.encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                owner = lock.read_text(encoding="utf-8")
                pid = owner.partition(":")[0]
                # ponytail: a dead owner's pid reused by a new process looks alive; the bot clears reels/*.lock at start
                if (not pid_alive(int(pid))) if pid.isdigit() else time.time() - lock.stat().st_mtime > stale_sec:
                    if lock.read_text(encoding="utf-8") == owner:
                        lock.unlink(missing_ok=True)
            except (FileNotFoundError, PermissionError):
                pass
            if time.time() > deadline:
                raise TimeoutError(f"{lock} is held by another process; try again")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            if lock.read_text(encoding="utf-8") == me:
                lock.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def locked_json(path, default, write=True):
    """Read a JSON file under its lock, yield it, write it back. A corrupt file raises instead of being replaced by
    the default, so a bad read can never wipe the library."""
    path = Path(path)
    with file_lock(path):
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
        yield data
        if write:
            write_json(path, data)


def load_config():
    """Saved settings over the defaults. Keeps every saved key (cloud_url too), so writing it back loses nothing."""
    saved = read_json(CONFIG, {}) or {}
    return {**json.loads(json.dumps(DEFAULTS)), **saved, "models": {**DEFAULTS["models"], **(saved.get("models") or {})}}


# Variables that tie a process to the Claude session that launched it. A build (or the bot) that inherits them
# runs as that session's "child": no saved transcript (so no --resume) and odd routing. Drop them for children.
SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID",
                "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_PID", "CLAUDE_CODE_ENTRYPOINT",
                "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SSE_PORT")


def cloud_link():
    """The cloud/pc_link.py module when this install uses cloud mode (reels/config.json has cloud_url), else None."""
    if not (read_json(CONFIG, {}) or {}).get("cloud_url"):
        return None
    try:
        sys.path.insert(0, str(ROOT / "cloud"))
        import pc_link  # noqa: PLC0415
        return pc_link
    except Exception:
        return None


def clean_env():
    return {k: v for k, v in os.environ.items() if k.upper() not in SESSION_VARS}


def is_local(model):
    """"local" or "local:<model name>": a plan or build by agent.py with an open model."""
    return str(model) == "local" or str(model).startswith("local:")


def load_env(path=None):
    """KEY=value lines of the project's .env into os.environ, never overriding what is already set."""
    path = Path(path or ROOT / ".env")
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        k, sep, v = line.partition("=")
        k = k.strip()
        if sep and k and not k.startswith("#") and k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")


def background():
    """Popen arguments for a child that must outlive its parent without flashing a window. Off Windows it gets
    its own process group, so kill_tree can stop it with everything it started."""
    return {"creationflags": NO_WINDOW} if os.name == "nt" else {"start_new_session": True}


def kill_tree(pid):
    """Stop a process and its children (a build's shell commands)."""
    if not pid:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
        return
    try:
        if os.getpgid(pid) == pid:  # started with background(): stop its whole group
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def find_exe(name):
    """Full path to claude / codex / bun. PATH first, then their usual install folders: Windows can start
    programs at login with a cut-off PATH that leaves these out, and a bare name would then fail."""
    found = shutil.which(name)
    if found:
        return found
    home, appdata = Path.home(), Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    for d in (home / ".local" / "bin", appdata / "npm", appdata / "npm" / "node_modules" / "bun" / "bin"):
        for ext in (".exe", ".cmd", ""):
            if (d / (name + ext)).is_file():
                return str(d / (name + ext))
    return name


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


# ---------------------------------------------------------------- Telegram
def _token():
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        return os.environ["TELEGRAM_BOT_TOKEN"].strip()
    env = TG_DIR / ".env"
    for row in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        k, _, v = row.partition("=")
        if k.strip() == "TELEGRAM_BOT_TOKEN":
            return v.strip().strip('"')
    return None


def tg_base():
    """The Bot API server: Telegram's, or a local one (REEL_TG_API, also used by the tests)."""
    return os.environ.get("REEL_TG_API", "https://api.telegram.org").rstrip("/")


def dry_log(line):
    REELS.mkdir(parents=True, exist_ok=True)
    with open(REELS / ".tg_dryrun.log", "a", encoding="utf-8") as f:
        f.write(f"--- {now()} {line}\n")


def tg_chats():
    return (read_json(TG_DIR / "access.json", {}) or {}).get("allowFrom", [])


def tg_send(text, chat_id=None, reply_to=None):
    """Send a message to the user (their own DM), formatted by tgfmt: light Markdown becomes bold/code/quotes and
    commands become one tap. Falls back to plain text if Telegram rejects the markup. Returns the first message id
    (truthy) if it went out, else None."""
    if os.environ.get("REEL_TG_DRYRUN"):  # tests: log instead of messaging the phone
        dry_log(f"to {chat_id or 'owner'}\n{text}")
        return True
    import tgfmt  # noqa: PLC0415 - next to this file
    token = _token()
    chats = [chat_id] if chat_id else tg_chats()
    if not token or not chats:
        return None
    first = None
    for chat in chats:
        for i, part in enumerate(tgfmt.chunks(text)):  # Telegram caps messages at 4096 chars
            body = {"chat_id": chat, "text": tgfmt.to_html(part), "parse_mode": "HTML",
                    "link_preview_options": {"is_disabled": True}}
            if reply_to and i == 0:
                body["reply_parameters"] = {"message_id": int(reply_to), "allow_sending_without_reply": True}
            for attempt in ("html", "plain"):
                try:
                    res = tg_api("sendMessage", body)
                    first = first or res.get("result", {}).get("message_id")
                    break
                except urllib.error.HTTPError as e:
                    if attempt == "html" and e.code == 400:  # markup Telegram can't parse: send it plain
                        body.pop("parse_mode")
                        body["text"] = tgfmt.plain(part)
                        continue
                    print(f"telegram send failed: {e}", file=sys.stderr)
                    break
                except Exception as e:
                    print(f"telegram send failed: {e}", file=sys.stderr)
                    break
    return first


def tg_api(method, params, timeout=30):
    token = _token()
    body = json.dumps(params).encode()
    req = urllib.request.Request(f"{tg_base()}/bot{token}/{method}", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def tg_send_file(path, chat_id=None, caption=None):
    """Send a file (a breakdown, a log, a diff) as a document. Returns True if it went out."""
    path = Path(path)
    if os.environ.get("REEL_TG_DRYRUN"):
        dry_log(f"file to {chat_id or 'owner'}: {path.name}")
        return True
    token, chats = _token(), [chat_id] if chat_id else tg_chats()
    if not token or not chats or not path.is_file():
        return False
    name = re.sub(r'[\r\n"\\]', "_", path.name)
    sent = False
    for chat in chats:
        boundary = uuid.uuid4().hex
        fields = {"chat_id": str(chat), **({"caption": caption[:1000]} if caption else {})}
        body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                        for k, v in fields.items())
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{name}"\r\n'
                 "Content-Type: application/octet-stream\r\n\r\n").encode() + path.read_bytes() + \
            f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"{tg_base()}/bot{token}/sendDocument", body,
                                     {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                sent = bool(json.load(r).get("ok")) or sent
        except Exception as e:
            print(f"telegram file send failed: {e}", file=sys.stderr)
    return sent


def tg_download(file_id, folder):
    """Save a Telegram attachment (the Bot API serves files up to 20 MB) and return its path."""
    remote = tg_api("getFile", {"file_id": file_id})["result"]["file_path"]
    dest = Path(folder) / f"{uuid.uuid4().hex[:8]}_{Path(remote).name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(f"{tg_base()}/file/bot{_token()}/{remote}", timeout=300) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


# ---------------------------------------------------------------- usage tracking
def pacific_now(utc=None):
    """Gemini free-tier quotas reset at midnight Pacific time. zoneinfo needs the tzdata package on Windows, so
    without it apply the US rule: daylight time from the 2nd Sunday of March to the 1st Sunday of November, 2:00."""
    utc = utc or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return utc.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:
        y = utc.year
        start = datetime(y, 3, 8 + (6 - datetime(y, 3, 8).weekday()) % 7, 10, tzinfo=timezone.utc)  # 2:00 PST
        end = datetime(y, 11, 1 + (6 - datetime(y, 11, 1).weekday()) % 7, 9, tzinfo=timezone.utc)  # 2:00 PDT
        return utc.astimezone(timezone(timedelta(hours=-7 if start <= utc < end else -8)))


def quota_day():
    return pacific_now().strftime("%Y-%m-%d")


def seconds_to_pacific_midnight():
    t = pacific_now()
    return int(86400 - (t.hour * 3600 + t.minute * 60 + t.second))


def gemini_usage_update(fn):
    """fn(day_record) mutates today's record: {"models": {m: {...}}, "exhausted": [...], "limits": {...}}."""
    with file_lock(GEMINI_USAGE):
        data = gemini_usage()
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
