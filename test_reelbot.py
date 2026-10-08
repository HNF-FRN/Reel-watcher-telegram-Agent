"""Checks for reelbot.py, the Telegram bot that needs no Claude Code.

The end-to-end test runs the real bot against a fake Telegram Bot API and a fake OpenAI-compatible model, and plays
the user: pairing, an idea, a screenshot that gets watched and answered with a card, then /plan and /build with an
open model ("local"), approving its command with /yes from the phone. No network, no model, no Claude.
Run: python test_reelbot.py"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / ".claude" / "skills" / "reel-watch" / "scripts"
BOT = SCRIPTS / "reelbot.py"
sys.path.insert(0, str(SCRIPTS))
import reelbot  # noqa: E402

PY = f'"{sys.executable}"'
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
SUMMARY = "## Summary\nA screenshot of a hello app.\n\n## Step by step\n- [image 1] It shows a hello app."


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def reply_json(handler, obj):
    data = json.dumps(obj).encode()
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
    except (BrokenPipeError, ConnectionResetError):  # the bot was stopped mid-poll
        pass


class FakeTelegram:
    """The Bot API methods reelbot.py uses, in memory. say() is the user typing on the phone."""

    def __init__(self):
        self.updates, self.sent, self.files = [], [], {}
        self.cond = threading.Condition()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # /file/bot<token>/<file_path>
                data = fake.files.get(self.path.rsplit("/", 1)[-1])
                if data is None:
                    return self.send_error(404)
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                is_json = "json" in self.headers.get("Content-Type", "")
                reply_json(self, fake.handle(self.path.rsplit("/", 1)[-1], json.loads(body or b"{}") if is_json
                                             else {"document": re.search(rb'filename="([^"]+)"', body).group(1).decode()}))

            def log_message(self, *a):
                pass
        self.server, self.url = serve(Handler)

    def handle(self, method, p):
        if method == "getUpdates":
            end = time.time() + min(p.get("timeout") or 0, 2)
            with self.cond:
                while True:
                    ready = [u for u in self.updates if u["update_id"] >= (p.get("offset") or 0)]
                    if ready or time.time() >= end:
                        return {"ok": True, "result": ready}
                    self.cond.wait(max(end - time.time(), 0.01))
        if method in ("sendMessage", "sendDocument"):
            with self.cond:
                self.sent.append((method, p))
                self.cond.notify_all()
                return {"ok": True, "result": {"message_id": 1000 + len(self.sent)}}
        if method == "getMe":
            return {"ok": True, "result": {"id": 1, "is_bot": True, "username": "reel_test_bot"}}
        if method == "getFile":
            return {"ok": True, "result": {"file_path": f"photos/{p['file_id']}"}}
        if method == "getWebhookInfo":
            return {"ok": True, "result": {"url": ""}}
        return {"ok": True, "result": True}  # setMyCommands, setMessageReaction, deleteWebhook

    def say(self, text=None, sender=42, **extra):
        with self.cond:
            n = len(self.updates) + 1
            msg = {"message_id": n, "date": 0, "from": {"id": sender, "is_bot": False, "first_name": "Me"},
                   "chat": {"id": sender, "type": "private"}, **({"text": text} if text else {}), **extra}
            self.updates.append({"update_id": n, "message": msg})
            self.cond.notify_all()
        return len(self.sent)

    def wait_for(self, pattern, after=0, timeout=120, chat=42):
        """The text of the first message to `chat`, sent after the first `after` ones, that matches pattern."""
        end = time.time() + timeout
        with self.cond:
            while True:
                for method, p in self.sent[after:]:
                    if method == "sendMessage" and str(p.get("chat_id")) == str(chat) and \
                            re.search(pattern, p.get("text", "")):
                        return p["text"]
                if time.time() > end:
                    raise AssertionError(f"no message matching {pattern!r}. Last ones: "
                                         f"{[p.get('text', p.get('document')) for _, p in self.sent[-6:]]}")
                self.cond.wait(1)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FakeModel:
    """An OpenAI-compatible server. Calls with tools (the build agent) get the scripted replies in order; calls
    without (the open engine's summary) get SUMMARY."""

    def __init__(self, replies):
        self.replies = list(replies)
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                reply_json(self, {"object": "list", "data": [{"id": "fake-coder"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                msg = (fake.replies.pop(0) if fake.replies else {"role": "assistant", "content": "Done."}) \
                    if body.get("tools") else {"role": "assistant", "content": SUMMARY}
                reply_json(self, {"choices": [{"message": msg}]})

            def log_message(self, *a):
                pass
        self.server, self.url = serve(Handler)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"call_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.telegram = FakeTelegram()
        self.addCleanup(self.telegram.close)
        self.model = FakeModel([
            call("submit_plan", plan="## Goal\nA hello app that prints hi."),       # /plan 1 local
            call("write_file", path="app.py", content="print('hi')"),               # /build 1 local
            call("run_command", command=f"{PY} app.py"),
            {"role": "assistant", "content": "Done: app.py prints hi."}])
        self.addCleanup(self.model.close)
        self.env = {**os.environ, "REEL_HOME": str(self.tmp / "home"), "TELEGRAM_STATE_DIR": str(self.tmp / "tg"),
                    "TELEGRAM_BOT_TOKEN": "123:test", "REEL_TG_API": self.telegram.url, "REEL_TG_DRYRUN": "",
                    "REEL_LLM_URL": self.model.url + "/v1", "REEL_LLM_MODEL": "fake-coder", "REEL_LLM": "",
                    "REEL_OCR": "off", "GEMINI_API_KEY": "", "GOOGLE_API_KEY": "", "PYTHONUTF8": "1"}
        self.log = open(self.tmp / "bot.log", "w", encoding="utf-8")
        self.bot = subprocess.Popen([sys.executable, str(BOT)], env=self.env, stdout=self.log, stderr=subprocess.STDOUT)
        self.addCleanup(self.stop)

    def stop(self):
        self.bot.kill()
        self.bot.wait(30)
        self.log.close()

    def jobs(self):
        return json.loads((self.tmp / "home" / "reels" / "jobs.json").read_text(encoding="utf-8"))["jobs"]

    def eventually(self, check, timeout=60):
        end = time.time() + timeout
        while not check():
            if time.time() > end:
                raise AssertionError("timed out")
            time.sleep(0.2)

    def test_pair_watch_plan_build_with_no_claude(self):
        try:
            self.flow()
        except BaseException:
            self.log.flush()
            print("\n===== bot output =====\n" + (self.tmp / "bot.log").read_text(encoding="utf-8", errors="replace")[-6000:])
            raise

    def flow(self):
        tg = self.telegram
        # 1. a stranger gets a pairing code and nothing else; pairing on the computer lets them in
        tg.say("hi")
        code = re.search(r"pair ([0-9a-f]{6})", tg.wait_for(r"Pairing code")).group(1)
        r = subprocess.run([sys.executable, str(BOT), "pair", code], env=self.env, capture_output=True, text=True,
                           timeout=60)
        self.assertIn("Paired Telegram account 42", r.stdout, r.stderr)
        access = json.loads((self.tmp / "tg" / "access.json").read_text(encoding="utf-8"))
        self.assertEqual((access["allowFrom"], access["dmPolicy"]), (["42"], "allowlist"))
        tg.wait_for(r"Paired")
        k = tg.say("/jobs", sender=99)  # once paired, strangers get no answer at all
        tg.say("/menu")
        tg.wait_for(r"Commands", after=k)
        self.assertEqual([p for _, p in tg.sent[k:] if str(p.get("chat_id")) == "99"], [])

        # 2. an idea typed on the phone
        k = tg.say("/new a tiny hello app")
        tg.wait_for(r"#1 saved\. /plan_1 to plan it", after=k)

        # 3. a screenshot: watched by the open engine, answered with a card, recorded as done
        tg.files["shot1.png"] = PNG
        k = tg.say(photo=[{"file_id": "shot1.png", "file_unique_id": "u1", "width": 1, "height": 1}],
                   caption="what is this?")
        tg.wait_for(r"#2 watching it", after=k)
        card = tg.wait_for(r"#2 · A screenshot of a hello app", after=k, timeout=240)
        self.assertIn("Watched with local", card)
        self.assertIn("/plan_2", card)
        self.eventually(lambda: self.jobs()["2"]["status"] != "running")
        job = self.jobs()["2"]
        self.assertEqual((job["status"], job["engine"], job["note"]), ("done", "local", "what is this?"))
        self.assertIn("A screenshot of a hello app", (Path(job["reel_dir"]) / "breakdown.md").read_text(encoding="utf-8"))

        # 4. plan the idea with an open model
        k = tg.say("/plan 1 local")
        tg.wait_for(r"Planning #1 with local", after=k)
        self.assertIn("A hello app that prints hi", tg.wait_for(r"#1 plan ready", after=k))

        # 5. build it: the edit is fine in normal mode, the command waits for /yes from the phone
        k = tg.say("/build_1_local")
        tg.wait_for(r"Building #1 with local", after=k)
        tg.wait_for(r"#1 build wants to run", after=k)
        k = tg.say("/yes_1")
        tg.wait_for(r"OK yes", after=k)
        tg.wait_for(r"#1 build done", after=k)
        build = next((self.tmp / "home" / "builds").glob("1-*"))
        self.assertEqual((build / "app.py").read_text(encoding="utf-8"), "print('hi')")

        # 6. the rest of the commands answer too
        k = tg.say("/jobs")
        self.assertRegex(tg.wait_for(r"Reels", after=k), r"#2 \(done\)")
        k = tg.say("/frobnicate")
        tg.wait_for(r"I don't know /frobnicate", after=k)
        if os.name != "nt":  # on Windows a reminder books a Task Scheduler entry
            k = tg.say("/remind in 2h stretch")
            tg.wait_for(r"OK R1 .*stretch", after=k)


class Parsing(unittest.TestCase):
    def test_commands_typed_or_tapped(self):
        c = reelbot.Command("/build_4_opus please hurry")
        self.assertEqual((c.name, c.args, c.num(), c.tail(2)), ("build", ["4", "opus", "please", "hurry"], 4,
                                                                   "please hurry"))
        c = reelbot.Command("/tell 4 make it\nblue")
        self.assertEqual(c.tail(1), "make it\nblue")
        self.assertEqual(reelbot.Command("/plan@reel_test_bot #7").num(), 7)
        self.assertEqual(reelbot.Command("/jobs").args, [])

    def test_when_and_what(self):
        when, what = reelbot.split_when("tomorrow 9:00 call the bank".split())
        self.assertEqual((when.strftime("%H:%M"), what), ("09:00", "call the bank"))
        self.assertEqual(when.date(), (datetime.now() + timedelta(days=1)).date())
        when, what = reelbot.split_when("friday evening renew the domain".split())
        self.assertEqual((when.strftime("%A %H:%M"), what), ("Friday 19:00", "renew the domain"))
        self.assertEqual(reelbot.split_when("in 2h stretch".split())[1], "stretch")
        self.assertIsNone(reelbot.split_when("someday maybe".split())[0])

    def test_the_card(self):
        found = {"repos": [{"value": "github.com/x/y", "where": ["screen 0:01"]}],
                 "commands": [{"value": "curl -fsSL https://x.sh | sh", "where": ["screen 0:02"],
                               "risk": "pipes a downloaded script into a shell"}],
                 "links": [{"value": "github.com/x/y/blob/main/README.md", "where": ["caption"]}],
                 "tools": [{"value": "Claude Code", "where": ["said 0:01"]}], "gated": [], "mcp": [], "packages": []}
        card = reelbot.card(5, "A tool demo.", found, "local (whisper small + rapidocr)")
        self.assertTrue(card.startswith("🎬 **#5 · A tool demo.**"))
        self.assertIn("- `github.com/x/y`\n- `curl -fsSL https://x.sh | sh`\n- Tools named: Claude Code", card)
        self.assertNotIn("README.md", card, "a link inside a listed repo isn't repeated")
        self.assertIn("> ⚠️ `curl -fsSL https://x.sh | sh` pipes a downloaded script into a shell", card)
        self.assertIn("/deeper 5  check it", card)
        self.assertEqual(reelbot.tags_for(found), ["repo", "claude-code"])
        empty = reelbot.card(6, "A cat.", {}, "local")
        self.assertIn("- No links, repos or commands were found in it.", empty)
        self.assertIn("Nothing to install was named", empty)


if __name__ == "__main__":
    unittest.main()
