"""reelbot.py - the Telegram bot without Claude Code. Runs on Windows, macOS and Linux.

    python reelbot.py              run the bot (long polling; Ctrl+C stops it)
    python reelbot.py pair CODE    approve your Telegram account (message the bot once: it answers with the code)
    python reelbot.py check        test the bot token and show who is paired

No model sits in its loop: every message is routed by fixed rules to the same scripts, commands and cards as the
Claude Code dispatcher (CLAUDE.md), so nothing in a reel can talk it into anything. Reels go to reel.py: Gemini if
GEMINI_API_KEY is set, otherwise the open engine (Whisper, OCR and, if one is running, a local model for the
summary). /plan and /build go to build.py: `local` is an open model driven by agent.py, with the same 🔐 approvals
on the phone, and it is the default when Claude Code isn't installed. /deeper checks the repos and packages a reel
names against GitHub, npm and PyPI (verify.py). Reminders go to remind.py, and the bot sends them when they come due.

Setup: TELEGRAM_BOT_TOKEN=<token from @BotFather> in the project's .env (or the environment), then run it.
REEL_HOME and TELEGRAM_STATE_DIR, if you use them, must be real environment variables.
"""
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze  # noqa: E402
import extract  # noqa: E402
import verify  # noqa: E402
from common import (BUILD_MODELS, BUILDS, CLAUDE_MODELS, MODES, NO_WINDOW, REELS, ROOT, SCRIPTS, TG_DIR,  # noqa: E402
                    _token, clean_env, file_lock, find_exe, load_config, load_env, read_json, tg_api, tg_download,
                    tg_send, tg_send_file, utf8_stdio, write_json)

JOBS_PY, BUILD_PY, MAINTAIN_PY, REEL_PY = (SCRIPTS / f for f in ("jobs.py", "build.py", "maintain.py", "reel.py"))
REMIND_PY = ROOT / "reminders" / "remind.py"
ACCESS = TG_DIR / "access.json"
OFFSET = REELS / ".bot_offset.json"
INCOMING = REELS / "incoming"   # files sent on Telegram, per job, so /retry can watch them again
PAIR_MINUTES = 60
LINK_RE = re.compile(r"https?://\S+", re.I)
SHORTCUTS = {"1": "plan", "2": "save", "3": "deeper"}
WATCH_SLOTS = threading.BoundedSemaphore(int(os.environ.get("REEL_WATCHERS") or 2))  # Whisper is CPU heavy
TIME_WORDS = {"morning": "9:00", "noon": "12:00", "afternoon": "15:00", "evening": "19:00", "tonight": "tonight"}
HELP = "Send me a reel or video link, a video file or screenshots and I'll break it down. /menu lists the commands."
FETCH_FAILED = ("I couldn't grab that one 😕 Open the reel → Share → Download, then send me the video file "
                "(for a photo post, screenshots of the slides).")
MENU = """📖 **Commands** · N is a reel number

🎬 **Reels**
Send a link, video or screenshots · /jobs · /saved
`/r N` breakdown · `/find words` · `/new idea`
`/deeper N` check it · `/retry N` · `/rewatch N deep`

🛠 **Build**
`/plan N` · `/build N [model]` · `/tell N msg`
🔐 `/yes N` · `/no N` · `/always N` · /pending
👀 /tasks · `/peek N` · `/diff N` · `/log N`
`/stop N` · `/resume N` · `/undo N` · `/deploy N`

⏰ **Remind**
`/remind tomorrow 9:00 call the bank`
`/todo buy domain` · /reminders · `/done R3` · `/snooze R3 1h`

⚙️ **Settings**
/models · `/model build local` · `/mode safe` · `/limit 45`
/quota · /pc · /digest · /manual

*Models: local (an open model on this computer) · haiku · sonnet · opus · fable · codex*"""


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def run(script, *args, timeout=900):
    """Run one of the project's scripts: (exit code, stdout, stderr)."""
    env = {**clean_env(), "PYTHONUTF8": "1"}
    try:
        r = subprocess.run([sys.executable, str(script), *[str(a) for a in args]], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, cwd=str(ROOT), env=env,
                           creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return 124, "", f"{Path(script).name} took longer than {timeout // 60} min"
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def said(script, *args, timeout=900):
    """What a script printed, to relay as is: its output, else the end of its error."""
    code, out, err = run(script, *args, timeout=timeout)
    if out:
        return out
    return "Done." if code == 0 else "\n".join(err.splitlines()[-3:]) or "That didn't work."


def jobs():
    return (read_json(REELS / "jobs.json", {}) or {}).get("jobs", {})


def latest(kind="reel"):
    """The most recent reel number, or the job number of the most recently active build."""
    if kind == "build":
        states = [read_json(d / ".reel" / "state.json", {}) or {} for d in BUILDS.glob("*-*")] if BUILDS.exists() else []
        return max((s for s in states if s.get("job")), key=lambda s: s.get("updated") or "", default={}).get("job")
    return max((int(n) for n in jobs()), default=None)


def default_model(kind):
    """None (build.py uses the saved setting) unless that setting needs a CLI that isn't installed: then "local"."""
    configured = load_config()["models"][kind]
    exe = "codex" if configured == "codex" else "claude" if configured in CLAUDE_MODELS else None
    return "local" if exe and not Path(find_exe(exe)).is_file() else None


# ---------------------------------------------------------------- access (same access.json as the Telegram plugin)
def access():
    a = read_json(ACCESS, None)
    return a if isinstance(a, dict) else {"dmPolicy": "pairing", "allowFrom": [], "groups": {}, "pending": {}}


def allowed(sender):
    return str(sender) in [str(x) for x in access().get("allowFrom") or []]


def pairing_code(sender, chat):
    """A code for an unknown sender to be approved with (None when pairing is off or they asked too often)."""
    now_ms = int(time.time() * 1000)
    with file_lock(ACCESS):
        a = access()
        if a.get("dmPolicy", "pairing") != "pairing":
            return None
        pending = {c: p for c, p in (a.get("pending") or {}).items() if (p.get("expiresAt") or 0) > now_ms}
        code = next((c for c, p in pending.items() if str(p.get("senderId")) == str(sender)), None)
        if code:
            if pending[code].get("replies", 1) >= 3:
                return None
            pending[code]["replies"] = pending[code].get("replies", 1) + 1
        elif len(pending) >= 3:  # strangers can't fill the file
            return None
        else:
            code = secrets.token_hex(3)
            pending[code] = {"senderId": str(sender), "chatId": str(chat), "createdAt": now_ms,
                             "expiresAt": now_ms + PAIR_MINUTES * 60_000, "replies": 1}
        a["pending"] = pending
        write_json(ACCESS, a)
    return code


def pair(code):
    """Approve the Telegram account that got this code: only it can use the bot from now on."""
    with file_lock(ACCESS):
        a = access()
        p = (a.get("pending") or {}).get(code.strip())
        if not p or (p.get("expiresAt") or 0) < time.time() * 1000:
            print("No pending pairing with that code. Message the bot again for a fresh one.")
            return 1
        allow = [str(x) for x in a.get("allowFrom") or []]
        a["allowFrom"] = allow + [str(p["senderId"])] if str(p["senderId"]) not in allow else allow
        del a["pending"][code.strip()]
        a["dmPolicy"] = "allowlist"
        write_json(ACCESS, a)
    (TG_DIR / "approved").mkdir(parents=True, exist_ok=True)
    (TG_DIR / "approved" / str(p["senderId"])).write_text(str(p["chatId"]), encoding="utf-8")
    tg_send("✅ **Paired.** Send me a reel link, a video or screenshots.\n/menu lists everything.", p["chatId"])
    print(f"Paired Telegram account {p['senderId']}. Only it can use the bot now.")
    return 0


def check():
    if not _token():
        print("No bot token: put TELEGRAM_BOT_TOKEN=<token from @BotFather> in .env")
        return 1
    try:
        me = tg_api("getMe", {})["result"]
    except Exception as e:
        print(f"Telegram didn't accept the token: {e}")
        return 1
    print(f"@{me.get('username')} works. Paired: {', '.join(map(str, access().get('allowFrom') or [])) or 'nobody yet'}")
    return 0


# ---------------------------------------------------------------- what a message holds
def media_of(msg):
    """(file_id, name) of the video or image in a message, else None."""
    if msg.get("photo"):
        return msg["photo"][-1]["file_id"], "photo.jpg"
    for key in ("video", "animation", "video_note"):
        if msg.get(key):
            return msg[key]["file_id"], msg[key].get("file_name") or f"{key}.mp4"
    doc = msg.get("document") or {}
    if str(doc.get("mime_type") or "").startswith(("video/", "image/")):
        return doc["file_id"], doc.get("file_name") or "file"
    return None


class Command:
    """"/build_4_opus please" -> name "build", args ["4", "opus", "please"]; "/plan@MyBot 4" works too."""

    def __init__(self, text):
        m = re.match(r"/([A-Za-z0-9_]+)(?:@\w+)?(?:\s+(.*))?$", text.strip(), re.S)
        parts = (m.group(1) if m else "").split("_")
        self.name = parts[0].lower()
        self.tapped = [p for p in parts[1:] if p]
        self.rest = ((m.group(2) if m else "") or "").strip()
        self.args = self.tapped + self.rest.split()

    def num(self, i=0):
        v = self.args[i].lstrip("#") if len(self.args) > i else ""
        return int(v) if v.isdigit() else None

    def tail(self, k):
        """Everything after the first k arguments, as typed."""
        skip = k - len(self.tapped)
        if skip <= 0:
            return " ".join(self.tapped[k:] + ([self.rest] if self.rest else []))
        parts = re.split(r"\s+", self.rest, maxsplit=skip)
        return parts[skip] if len(parts) > skip else ""


def split_when(words):
    """The longest start of `words` that remind.py reads as a time: (datetime, the rest as text)."""
    sys.path.insert(0, str(REMIND_PY.parent))
    import remind  # noqa: PLC0415
    for k in range(min(4, len(words) - 1), 0, -1):
        when = re.sub(r"^(?:on|at)\s+", "", " ".join(TIME_WORDS.get(w.lower(), w) for w in words[:k]), flags=re.I)
        try:
            return remind.parse_when(when), " ".join(words[k:])
        except SystemExit:
            continue
    return None, " ".join(words)


# ---------------------------------------------------------------- the breakdown card
def clean(value):
    return str(value).replace("`", "'").replace("\n", " ")


def summary_line(analysis, fallback="a reel"):
    s = re.sub(r"\s+", " ", analyze.summary_of(analysis) or "").replace("**", "").strip()
    return (s[:97].rstrip() + "…" if len(s) > 98 else s) or fallback


def card(n, summary, found, engine):
    """The reply to a watched reel, in the same shape as the Claude Code worker's."""
    repos = [r["value"] for r in found.get("repos") or []]
    cmds = [c["value"] for c in found.get("commands") or []]
    shows = [f"`{clean(r)}`" for r in repos[:3]] + [f"`{clean(c)}`" for c in cmds[:3]]
    shows += [f"MCP server `{clean(m['value'])}`" for m in (found.get("mcp") or [])[:2]]
    shows += [f"`{clean(p['value'])}`" for p in (found.get("packages") or [])
              if not any(p["value"].split(": ")[-1] in c for c in cmds)][:2]
    shows += [f"`{clean(x['value'])}`" for x in found.get("links") or [] if not extract.covered(x["value"], repos)][:2]
    tools = [t["value"] for t in found.get("tools") or []]
    if tools:
        shows.append("Tools named: " + ", ".join(tools[:6]))
    if found.get("mcp"):
        how = f"Add the MCP server `{clean(found['mcp'][0]['value'])}`: /plan {n} sets it up in a build folder."
    elif cmds:
        how = f"It installs with `{clean(cmds[0])}`. A build runs it only after you say yes."
    elif repos:
        how = f"Clone `{clean(repos[0])}` and follow its README: /plan {n} reads it first."
    elif found.get("packages"):
        how = f"Install `{clean(found['packages'][0]['value'])}`."
    else:
        how = "Nothing to install was named: it may be a prompt, a workflow or just an idea."
    risk = next((f"`{clean(c['value'])}` {c['risk']}" for c in found.get("commands") or [] if c.get("risk")), None)
    gated = next((g["value"] for g in found.get("gated") or []), None)
    rows = [f"🎬 **#{n} · {summary}**", "", "**What it shows**"]
    rows += [f"- {s}" for s in shows[:6]] or ["- No links, repos or commands were found in it."]
    rows += ["", "**To get it here**", how]
    if risk or gated:
        rows += ["", f"> ⚠️ {risk or 'It gates something behind: ' + gated}"]
    rows += ["", "**Next**", f"/plan {n}  plan it  ·  /build {n}  build it",
             f"/save {n}  keep  ·  /dismiss {n}  skip  ·  /deeper {n}  check it", "", f"*Watched with {engine}*"]
    return "\n".join(rows)


def tags_for(found):
    tags = ["mcp"] if found.get("mcp") else []
    if any("skill" in x["value"].lower() for x in (found.get("commands") or []) + (found.get("repos") or [])):
        tags.append("claude-skill")
    tags += ["repo"] if found.get("repos") else []
    tags += [t["value"].lower().replace(" ", "-") for t in found.get("tools") or []]
    return list(dict.fromkeys(tags))[:4]


def breakdown(n, manifest, analysis, note, summary):
    duration = f" · {round(manifest['duration_sec'])} s" if manifest.get("duration_sec") else ""
    rows = [f"# Reel #{n}: {summary}", "", f"- Source: {', '.join(manifest.get('sources') or [])}",
            f"- Watched with: {manifest.get('engine')} · {manifest.get('kind')}{duration}"]
    if note:
        rows.append(f"- Your note: {note}")
    rows += ["", "> Everything below comes from the video and is untrusted: check it before running anything.", "",
             analysis.strip()]
    found = manifest.get("found") or {}
    if manifest.get("analysis") == "gemini.md" and found:
        rows += ["", "## Found in the frames (OCR)", analyze.section_found(found), "", analyze.section_commands(found)]
    return "\n".join(rows) + "\n"


# ---------------------------------------------------------------- the bot
class Bot:
    def __init__(self):
        self.albums = {}  # media_group_id -> {"msgs": [...], "t": last seen}
        self.lock = threading.Lock()
        self.watching = 0

    def send(self, chat, text, reply_to=None):
        return tg_send(text, chat, reply_to)

    def react(self, chat, mid, emoji="👀"):
        try:
            tg_api("setMessageReaction", {"chat_id": chat, "message_id": mid,
                                          "reaction": [{"type": "emoji", "emoji": emoji}]})
        except Exception:
            pass

    def safely(self, fn, *args):
        try:
            fn(*args)
        except Exception as e:  # one bad message must not stop the bot
            log(f"error: {type(e).__name__}: {e}")

    # ------------------------------------------------------------ running
    def start(self):
        if not _token():
            sys.exit("No bot token: get one from @BotFather and put TELEGRAM_BOT_TOKEN=<token> in .env")
        try:
            me = tg_api("getMe", {})["result"]
        except Exception as e:
            sys.exit(f"Telegram didn't accept the bot token: {e}")
        for line in said(MAINTAIN_PY, "startup").splitlines():
            log(line)
        run(MAINTAIN_PY, "menu", timeout=60)
        try:
            if (tg_api("getWebhookInfo", {}).get("result") or {}).get("url"):
                log("a webhook was set (cloud mode): this bot takes the messages while it runs")
                tg_api("deleteWebhook", {})
        except Exception as e:
            log(f"couldn't check the webhook: {e}")
        log(f"@{me.get('username')} is running on this computer. Ctrl+C stops it.")
        if not access().get("allowFrom"):
            log("nobody is paired yet: message the bot on Telegram, then run: python reelbot.py pair <code>")
        threading.Thread(target=self.ticker, daemon=True).start()
        try:
            self.poll()
        except KeyboardInterrupt:
            log("stopped")

    def poll(self):
        offset = (read_json(OFFSET, {}) or {}).get("offset")
        conflicts = 0
        while True:
            wait = 1 if self.albums else 25
            params = {"timeout": wait, "allowed_updates": ["message"], **({"offset": offset} if offset else {})}
            try:
                res = tg_api("getUpdates", params, timeout=wait + 15)
                conflicts = 0
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    sys.exit("Telegram refused the bot token (401): check TELEGRAM_BOT_TOKEN.")
                if e.code == 409 and conflicts % 12 == 0:
                    log("another program is reading this bot's messages (the Claude Code bot?); waiting")
                conflicts += e.code == 409
                time.sleep(5)
                continue
            except Exception as e:
                log(f"no answer from Telegram ({e}); retrying")
                time.sleep(5)
                continue
            for upd in res.get("result") or []:
                offset = upd["update_id"] + 1
                write_json(OFFSET, {"offset": offset})
                self.safely(self.on_update, upd)
            self.flush_albums()

    def ticker(self):
        """Due reminders. On Windows, Task Scheduler sends them (remind.py books an entry per reminder)."""
        while True:
            time.sleep(60)
            if os.name != "nt" and REMIND_PY.exists():
                run(REMIND_PY, "send-due", timeout=120)

    # ------------------------------------------------------------ messages
    def on_update(self, upd):
        msg = upd.get("message")
        if not msg or (msg.get("chat") or {}).get("type") != "private" or not msg.get("from"):
            return
        sender, chat = msg["from"]["id"], msg["chat"]["id"]
        if not allowed(sender):
            code = pairing_code(sender, chat)
            if code:
                self.send(chat, f"🔐 **Pairing code: `{code}`**\nOn the computer running the bot:\n"
                                f"`python reelbot.py pair {code}`\n(or `npx reel-agent pair {code}`). "
                                f"It expires in {PAIR_MINUTES} min.")
            return
        if msg.get("media_group_id") and media_of(msg):  # an album arrives as one message per item
            with self.lock:
                album = self.albums.setdefault(msg["media_group_id"], {"msgs": [], "t": 0})
                album["msgs"].append(msg)
                album["t"] = time.time()
            return
        self.on_message(msg)

    def flush_albums(self):
        now = time.time()
        with self.lock:
            ready = [k for k, a in self.albums.items() if now - a["t"] > 1.5]
            albums = [self.albums.pop(k) for k in ready]
        for a in albums:
            msgs = sorted(a["msgs"], key=lambda m: m["message_id"])
            self.safely(self.on_message, msgs[0], msgs[1:])

    def on_message(self, msg, more=()):
        chat, mid = msg["chat"]["id"], msg["message_id"]
        msgs = [msg, *more]
        text = next((m.get("text") or m.get("caption") for m in msgs if m.get("text") or m.get("caption")), "").strip()
        media = [m for m in msgs if media_of(m)]
        if media:
            return self.new_reel(chat, mid, files=[media_of(m) for m in media], note=text)
        if text.startswith("/"):
            return self.command(chat, mid, text)
        link = LINK_RE.search(text)
        if link:
            return self.new_reel(chat, mid, link=link.group(0), note=(text[:link.start()] + text[link.end():]).strip())
        m = re.fullmatch(r"#?(\d+)\s+([123])", text)
        if m:
            return self.command(chat, mid, f"/{SHORTCUTS[m.group(2)]} {m.group(1)}")
        m = re.fullmatch(r"#?(\d+)[\s,:.-]+(.+)", text, re.S)
        if m:
            return self.about_reel(chat, mid, int(m.group(1)), m.group(2).strip())
        self.send(chat, HELP, mid)

    def about_reel(self, chat, mid, n, words):
        """"4 make it a skill" or "#4 is this legit?": the words are the user's own instruction for reel #4."""
        if re.search(r"\b(?:build|make|set (?:it )?up|install|create|implement|do it|turn it into)\b", words, re.I):
            return self.command(chat, mid, f"/plan {n} {words}")
        if re.search(r"\b(?:research|check|real|legit|scam|safe|cost|price|free|alternatives?|deeper)\b", words, re.I):
            return self.command(chat, mid, f"/deeper {n}")
        j = jobs().get(str(n))
        if not j:
            return self.send(chat, f"No reel #{n}. /jobs lists them.", mid)
        self.send(chat, f"🎬 **#{n} · {j.get('summary') or j['source'][:80]}**\n\nThis bot follows commands, it "
                        f"doesn't chat. Try:\n/r {n} full  the whole breakdown\n/deeper {n}  check what it names\n"
                        f"`/plan {n} <what you want>`  plan a build", mid)

    # ------------------------------------------------------------ watching
    def new_reel(self, chat, mid, link=None, files=(), note=""):
        source = link or f"telegram {'photos' if all(n == 'photo.jpg' for _, n in files) else 'file'}: " + \
            ", ".join(n for _, n in files)
        code, out, err = run(JOBS_PY, "new", "--source", source, "--chat", chat, "--msg", mid, "--note", note)
        dup = re.match(r"DUPLICATE (\d+) (\S+)", out)
        if dup:
            n, status = dup.groups()
            if status == "running":
                return self.send(chat, f"#{n} is still being watched.", mid)
            return self.send(chat, f"Already watched that one, it's #{n}: {jobs().get(n, {}).get('summary') or ''}"
                                   f"\n/r {n}  see it", mid)
        m = re.match(r"JOB (\d+)", out)
        if not m:
            return self.send(chat, f"Couldn't start: {(err or out)[-300:]}", mid)
        n = int(m.group(1))
        self.react(chat, mid)
        busy = self.watching
        self.send(chat, f"#{n} watching it…" + (f" ({busy} other{'s' if busy > 1 else ''} running)" if busy else ""))
        threading.Thread(target=self.watch, args=(n, chat, mid), kwargs={"link": link, "files": files, "note": note},
                         daemon=True).start()

    def download(self, n, files):
        """Save the Telegram files of job N (in order) where /retry finds them again."""
        folder = INCOMING / str(n)
        paths = []
        for i, (file_id, _) in enumerate(files, 1):
            try:
                got = tg_download(file_id, folder)
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    raise RuntimeError("Telegram lets bots download files up to 20 MB: send a link instead") from None
                raise
            paths.append(got.replace(folder / f"{i:02d}{got.suffix.lower() or '.bin'}"))
        return [str(p) for p in paths]

    def watch(self, n, chat, mid, link=None, files=(), local=(), note="", extra=()):
        with self.lock:
            self.watching += 1
        try:
            with WATCH_SLOTS:
                sources = [link] if link else list(local) or self.download(n, files)
                code, out, err = run(REEL_PY, *sources, *extra, timeout=3600)
                if code == 3:
                    run(JOBS_PY, "fail", n, "--error", "download blocked")
                    return self.send(chat, f"#{n}: {FETCH_FAILED}", mid)
                m = re.search(r"^REEL_DIR: (.+)$", out, re.M)
                if code != 0 or not m:
                    last = (err or out).strip().splitlines()
                    raise RuntimeError(last[-1][:200] if last else f"reel.py stopped with code {code}")
                self.finish(n, chat, mid, Path(m.group(1).strip()), note)
        except Exception as e:
            run(JOBS_PY, "fail", n, "--error", str(e)[:200])
            self.send(chat, f"❌ #{n}: watching failed: {str(e)[:200]}\n\n/retry {n}  try again", mid)
        finally:
            with self.lock:
                self.watching -= 1

    def finish(self, n, chat, mid, work, note):
        manifest = read_json(work / "manifest.json", {}) or {}
        analysis = (work / (manifest.get("analysis") or "local.md")).read_text(encoding="utf-8", errors="replace")
        found, engine = manifest.get("found") or {}, manifest.get("engine") or "?"
        summary = summary_line(analysis)
        (work / "breakdown.md").write_text(breakdown(n, manifest, analysis, note, summary), encoding="utf-8")
        reply = card(n, summary, found, engine)
        (work / "reply.md").write_text(reply, encoding="utf-8")
        sent = self.send(chat, reply, mid)
        run(JOBS_PY, "done", n, "--dir", work, "--summary", summary, "--tags", ",".join(tags_for(found)),
            "--engine", "gemini" if engine.startswith("gemini") else "local",
            *(["--reply-msg", sent] if isinstance(sent, int) and not isinstance(sent, bool) else []))

    # ------------------------------------------------------------ commands
    def command(self, chat, mid, text):
        c = Command(text)
        handler = getattr(self, f"c_{c.name}", None)
        if not c.name or not handler:
            return self.send(chat, f"I don't know /{c.name}. /menu lists the commands.", mid)
        handler(chat, mid, c)

    def target(self, chat, mid, c, kind="reel"):
        """(N, index of the next argument). N defaults to the latest reel or build."""
        n = c.num()
        if n is not None:
            return n, 1
        n = latest(kind)
        if n is None:
            self.send(chat, f"Which number? e.g. /{c.name} 4", mid)
        return n, 0

    def relay(self, chat, mid, script, *args, title=None, timeout=900):
        out = said(script, *args, timeout=timeout)
        self.send(chat, f"{title}\n{out}" if title else out, mid)

    # reels and library
    def c_menu(self, chat, mid, c):
        self.send(chat, MENU, mid)

    c_start = c_help = c_menu

    def c_jobs(self, chat, mid, c):
        self.relay(chat, mid, JOBS_PY, "list", "--limit", "10", title="🎬 **Reels**")

    def c_saved(self, chat, mid, c):
        self.relay(chat, mid, JOBS_PY, "list", "--status", "saved", title="⭐ **Saved**")

    def c_find(self, chat, mid, c):
        if not c.args:
            return self.send(chat, "Say what to look for: `/find mcp notion`", mid)
        self.relay(chat, mid, JOBS_PY, "search", *c.args, title=f"🔍 **{clean(c.rest or ' '.join(c.args))}**")

    def c_r(self, chat, mid, c):
        n, i = self.target(chat, mid, c)
        if n is None:
            return
        j = jobs().get(str(n))
        if not j:
            return self.send(chat, f"No reel #{n}. /jobs lists them.", mid)
        d = Path(j.get("reel_dir") or "")
        if j.get("reel_dir") and "full" in [a.lower() for a in c.args[i:]] and (d / "breakdown.md").exists():
            tg_send_file(d / "breakdown.md", chat, caption=f"#{n} · {j.get('summary') or ''}"[:200])
        elif j.get("reel_dir") and (d / "reply.md").exists():
            self.send(chat, (d / "reply.md").read_text(encoding="utf-8"), mid)
        elif j.get("reel_dir") and (d / "breakdown.md").exists():
            self.send(chat, f"🎬 **#{n} · {j.get('summary') or ''}**\n\n/r {n} full  the whole breakdown\n"
                            f"/plan {n}  plan it", mid)
        else:
            self.send(chat, f"#{n} has no breakdown yet ({j.get('status')}).", mid)

    def c_deeper(self, chat, mid, c):
        n, _ = self.target(chat, mid, c)
        if n is None:
            return
        j = jobs().get(str(n))
        if not j or not j.get("reel_dir"):
            return self.send(chat, f"#{n} has no breakdown yet.", mid)
        self.send(chat, f"🔎 Checking what #{n} names on GitHub, npm and PyPI…", mid)
        threading.Thread(target=self.safely, args=(self.deeper, n, chat, mid, Path(j["reel_dir"])), daemon=True).start()

    def deeper(self, n, chat, mid, folder):
        results = verify.check((read_json(folder / "manifest.json", {}) or {}).get("found") or {})
        (folder / "research.md").write_text(verify.research_md(n, results), encoding="utf-8")
        self.send(chat, verify.card(n, results), mid)
        if results:
            tg_send_file(folder / "research.md", chat, caption=f"#{n}: everything the registries said")

    def c_tag(self, chat, mid, c):
        n, i = self.target(chat, mid, c)
        if n is not None:
            self.relay(chat, mid, JOBS_PY, "tag", n, "--tags", ",".join(c.args[i:]).replace(",,", ","))

    def set_status(self, chat, mid, c, status, words):
        n, _ = self.target(chat, mid, c)
        if n is not None:
            code, out, err = run(JOBS_PY, "set", n, "--status", status)
            self.send(chat, f"#{n} {words}." if code == 0 else (err or out), mid)

    def c_save(self, chat, mid, c):
        self.set_status(chat, mid, c, "saved", "saved: /saved lists it")

    def c_dismiss(self, chat, mid, c):
        self.set_status(chat, mid, c, "dismissed", "dismissed")

    def c_retry(self, chat, mid, c, extra=()):
        n, _ = self.target(chat, mid, c)
        if n is None:
            return
        j = jobs().get(str(n))
        if not j:
            return self.send(chat, f"No reel #{n}. /jobs lists them.", mid)
        if j["source"].startswith("idea:"):
            return self.send(chat, f"#{n} is an idea, not a reel: /plan {n} plans it.", mid)
        files = sorted(str(f) for f in (INCOMING / str(n)).glob("*")) if (INCOMING / str(n)).exists() else []
        if not files and not j["source"].startswith("http"):
            return self.send(chat, f"I no longer have #{n}'s file: send it again.", mid)
        run(JOBS_PY, "set", n, "--status", "running")
        self.send(chat, f"#{n} watching it again…", mid)
        threading.Thread(target=self.watch, args=(n, chat, mid), daemon=True,
                         kwargs={"link": None if files else j["source"], "local": files, "note": j.get("note") or "",
                                 "extra": extra}).start()

    def c_rewatch(self, chat, mid, c):
        how = [a.lower() for a in c.args]
        extra = ["--engine", "local"] if "local" in how else ["--check-frames", "16", "--max-frames", "30"]
        self.c_retry(chat, mid, c, extra=extra)

    def c_new(self, chat, mid, c):
        if not c.rest:
            return self.send(chat, "Say the idea: `/new a bot that sums up my emails`", mid)
        out = said(JOBS_PY, "idea", c.rest, "--chat", chat)
        m = re.match(r"JOB (\d+)", out)
        self.send(chat, f"#{m.group(1)} saved. /plan {m.group(1)} to plan it" if m else out, mid)

    def c_digest(self, chat, mid, c):
        self.relay(chat, mid, MAINTAIN_PY, "digest")

    def c_manual(self, chat, mid, c):
        if not tg_send_file(ROOT / "MANUAL.md", chat, caption="The manual"):
            self.send(chat, "Couldn't send MANUAL.md.", mid)

    # plans and builds
    def c_plan(self, chat, mid, c, kind="plan"):
        n, i = self.target(chat, mid, c)
        if n is None:
            return
        word = c.args[i].lower() if len(c.args) > i else ""
        model = word if word in BUILD_MODELS or word.startswith(("local:", "claude-")) else None
        j, mode, limit = i + bool(model), None, None
        while kind == "build" and j < len(c.args):
            w = c.args[j].lower()
            if w in MODES:
                mode = w
            elif re.fullmatch(r"\d+m(?:in)?", w):
                limit = int(re.match(r"\d+", w).group())
            else:
                break
            j += 1
        note = c.tail(j)
        model = model or default_model(kind)
        args = ["plan" if kind == "plan" else "start", n, *(["--model", model] if model else []),
                *(["--mode", mode] if mode else []), *(["--limit", limit] if limit else []),
                *(["--note", note] if note else []),
                *(["--fresh"] if kind == "build" and re.search(r"\bfrom scratch\b", note, re.I) else [])]
        code, out, err = run(BUILD_PY, *args, timeout=120)
        if code != 0:
            return self.send(chat, out or "\n".join(err.splitlines()[-3:]), mid)
        if kind == "plan":
            return self.send(chat, f"📋 Planning #{n} with {model or load_config()['models']['plan']}…", mid)
        self.send(chat, "🛠 " + re.sub(r"^OK |\s*Folder: .*$", "", out, flags=re.S), mid)

    def c_build(self, chat, mid, c):
        self.c_plan(chat, mid, c, kind="build")

    def c_tell(self, chat, mid, c):
        n, i = self.target(chat, mid, c, "build")
        if n is None:
            return
        if not c.tail(i):
            return self.send(chat, f"Say what to tell it: `/tell {n} use a dark theme`", mid)
        self.relay(chat, mid, BUILD_PY, "tell", n, c.tail(i))

    def answer(self, chat, mid, c, decision):
        n, i = self.target(chat, mid, c, "build")
        if n is not None:
            why = c.tail(i) if decision == "no" else ""
            self.relay(chat, mid, BUILD_PY, "answer", n, decision, *(["--reason", why] if why else []))

    def c_yes(self, chat, mid, c):
        self.answer(chat, mid, c, "yes")

    def c_no(self, chat, mid, c):
        self.answer(chat, mid, c, "no")

    def c_always(self, chat, mid, c):
        self.answer(chat, mid, c, "always")

    def c_pending(self, chat, mid, c):
        self.relay(chat, mid, BUILD_PY, "pending")

    def c_tasks(self, chat, mid, c):
        self.relay(chat, mid, BUILD_PY, "tasks")

    def build_cmd(self, chat, mid, c, name, *more):
        n, i = self.target(chat, mid, c, "build")
        if n is not None:
            self.relay(chat, mid, BUILD_PY, name, n, *more)
        return n, i

    def c_peek(self, chat, mid, c):
        self.build_cmd(chat, mid, c, "peek")

    def c_stop(self, chat, mid, c):
        self.build_cmd(chat, mid, c, "stop")

    def c_resume(self, chat, mid, c):
        n, i = self.target(chat, mid, c, "build")
        if n is not None:
            self.relay(chat, mid, BUILD_PY, "resume", n, *([c.tail(i)] if c.tail(i) else []))

    def c_diff(self, chat, mid, c):
        n, _ = self.target(chat, mid, c, "build")
        if n is None:
            return
        out = said(BUILD_PY, "diff", n)
        path = re.search(r"^DIFF_FILE: (.+)$", out, re.M)
        stat = re.sub(r"^DIFF_FILE: .+$", "", out, flags=re.M).strip()
        self.send(chat, f"🔍 **#{n} changes**\n```\n{stat[-3000:]}\n```" if path else out, mid)
        if path:
            tg_send_file(path.group(1).strip(), chat, caption=f"#{n} diff")

    def c_log(self, chat, mid, c):
        n, i = self.target(chat, mid, c, "build")
        if n is None:
            return
        out = said(BUILD_PY, "log", n)
        files = dict(re.findall(r"^(LOG_FILE|PLAN\.md|README\.md): (.+)$", out, re.M))
        if "LOG_FILE" not in files:
            return self.send(chat, out, mid)
        tg_send_file(files["LOG_FILE"].strip(), chat, caption=f"#{n} build log")
        if "plan" in [a.lower() for a in c.args[i:]] and "PLAN.md" in files:
            tg_send_file(files["PLAN.md"].strip(), chat, caption=f"#{n} plan")

    def confirmed(self, chat, mid, c, name, verb):
        """/undo and /deploy: first show what would happen, act only on "/undo N yes"."""
        n, i = self.target(chat, mid, c, "build")
        if n is None:
            return
        if [a.lower() for a in c.args[i:i + 1]] == ["yes"]:
            return self.relay(chat, mid, BUILD_PY, name, n, "--yes")
        out = said(BUILD_PY, name, n).replace("Confirm with the user, then run with --yes.", "").strip()
        if out.startswith(("This deletes", "Would copy")):
            out += f"\n\n/{name} {n} yes  {verb}"
        self.send(chat, out, mid)

    def c_undo(self, chat, mid, c):
        self.confirmed(chat, mid, c, "undo", "throw it away")

    def c_deploy(self, chat, mid, c):
        self.confirmed(chat, mid, c, "deploy", "install it")

    # reminders
    def c_remind(self, chat, mid, c):
        words, every = c.rest.split(), []
        if len(words) > 2 and words[0].lower() == "every" and words[1].lower() in ("day", "weekday", "week", "month"):
            every, words = ["--every", words[1].lower()], words[2:]
        when, what = split_when(words)
        if not when or not what:
            return self.send(chat, "Say when, then what: `/remind tomorrow 9:00 call the bank`, "
                                   "`/remind in 2h stretch`, `/remind every weekday 9:00 standup`", mid)
        self.relay(chat, mid, REMIND_PY, "add", what, "--at", when.strftime("%Y-%m-%d %H:%M"), "--source", "telegram",
                   *every, title="⏰")

    def c_todo(self, chat, mid, c):
        if c.rest:
            return self.relay(chat, mid, REMIND_PY, "add", c.rest, "--source", "telegram", title="📝")
        self.relay(chat, mid, REMIND_PY, "list")

    def c_reminders(self, chat, mid, c):
        self.relay(chat, mid, REMIND_PY, "list", title="⏰ **Reminders and to-dos**")

    def c_done(self, chat, mid, c):
        if not c.args:
            return self.send(chat, "Which one? e.g. /done R3", mid)
        self.relay(chat, mid, REMIND_PY, "done", c.args[0])

    def c_snooze(self, chat, mid, c):
        if len(c.args) < 2:
            return self.send(chat, "Which one, and for how long? e.g. /snooze R3 1h", mid)
        self.relay(chat, mid, REMIND_PY, "snooze", c.args[0], *c.args[1:])

    # settings and status
    def c_quota(self, chat, mid, c):
        self.relay(chat, mid, BUILD_PY, "quota", *(["--refresh"] if "refresh" in [a.lower() for a in c.args] else []))

    def c_models(self, chat, mid, c):
        self.relay(chat, mid, BUILD_PY, "config")

    def c_model(self, chat, mid, c):
        self.relay(chat, mid, BUILD_PY, "config", "model", *c.args[:2])

    def c_mode(self, chat, mid, c):
        self.relay(chat, mid, BUILD_PY, "config", "mode", *c.args[:1])

    def c_limit(self, chat, mid, c):
        self.relay(chat, mid, BUILD_PY, "config", "limit", *c.args[:1])

    def c_timeout(self, chat, mid, c):
        self.relay(chat, mid, BUILD_PY, "config", "timeout", *c.args[:1])

    def c_budget(self, chat, mid, c):
        self.relay(chat, mid, BUILD_PY, "config", "budget", *c.args[:1])

    def c_pc(self, chat, mid, c):
        self.relay(chat, mid, BUILD_PY, "pc")


def main():
    utf8_stdio()
    load_env()
    args = sys.argv[1:]
    if args[:1] == ["pair"] and len(args) == 2:
        sys.exit(pair(args[1]))
    if args == ["check"]:
        sys.exit(check())
    if args:
        sys.exit(__doc__)
    Bot().start()


if __name__ == "__main__":
    main()
