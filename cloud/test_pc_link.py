"""pc_link.py against a local copy of the Worker (test_server.mjs), with a throwaway reel library and
reminder list. Needs Node and `npm install` in cloud/. Run: python test_pc_link.py (in cloud/)."""
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pc_link  # noqa: E402

jobs = pc_link.jobs_module()
remind = pc_link.remind_module()


class PcLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = subprocess.Popen(["node", str(HERE / "test_server.mjs")], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, text=True)
        line = cls.server.stdout.readline()
        assert line.startswith("READY "), line
        cls.url = line.split()[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.stdin.close()
        cls.server.wait(timeout=20)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        reels, rem = self.tmp / "reels", self.tmp / "reminders"
        reels.mkdir(), rem.mkdir()
        (reels / "config.json").write_text(json.dumps({"cloud_url": self.url}))
        d = reels / "20260920-100000_abc"
        d.mkdir()
        (d / "breakdown.md").write_text("# MCP server trick\nuse some/repo", encoding="utf-8")
        (reels / "jobs.json").write_text(json.dumps({"next": 4, "jobs": {
            "1": {"source": "https://instagram.com/reel/a", "status": "done", "summary": "MCP server trick",
                  "reel_dir": str(d), "tags": ["mcp"], "created": "2026-09-20 10:00"},
            "2": {"source": "https://instagram.com/reel/b", "status": "dismissed", "created": "2026-09-21 10:00"},
            "3": {"source": "idea: x", "status": "saved", "summary": "idea x", "created": "2026-09-22 10:00"},
        }}))
        (rem / "reminders.json").write_text(json.dumps({"next": 2, "items": {
            "R1": {"text": "pay rent", "due": "2030-01-01 09:00", "every": None, "status": "open", "sent": None},
        }}))
        patches = [
            (pc_link, "REELS", reels), (pc_link, "CONFIG", reels / "config.json"),
            (pc_link, "SYNC_STATE", reels / ".cloud_sync.json"), (pc_link, "_token", lambda: "123:TEST"),
            (jobs, "REELS", reels), (jobs, "JOBS", reels / "jobs.json"), (jobs, "INDEX", reels / "INDEX.md"),
            (jobs, "LOCK", reels / ".jobs.lock"), (remind, "DATA", rem / "reminders.json"),
            (remind, "VIEW", rem / "REMINDERS.md"), (remind, "LOCK", rem / ".lock"),
            (remind, "sync", lambda data, **k: 0),  # never touch the real Task Scheduler
        ]
        self.saved = [(m, k, getattr(m, k)) for m, k, _ in patches]
        for m, k, v in patches:
            setattr(m, k, v)
        self.reels, self.rem = reels, rem

    def tearDown(self):
        for m, k, v in self.saved:
            setattr(m, k, v)

    def telegram(self, text, update_id):
        body = json.dumps({"update_id": update_id, "message": {
            "message_id": update_id, "chat": {"id": 42, "type": "private"}, "from": {"id": 42}, "text": text}})
        req = urllib.request.Request(self.url + "/telegram", body.encode(), method="POST", headers={
            "x-telegram-bot-api-secret-token": "hook-secret", "content-type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()

    def test_round_trip(self):
        # 1. The PC pushes its library; unchanged reels are not sent twice.
        self.assertEqual(pc_link.push(), (3, True))
        self.assertEqual(pc_link.push(), (0, False))
        # 2. While the PC is "off", the cloud saves an idea and a reminder, continuing the PC's numbering.
        self.telegram("/new a tiny CLI", 9001)
        self.telegram("/remind 2030-02-01 10:00 call the bank", 9002)
        self.telegram("/save 2", 9003)
        # 3. The PC comes back and pulls.
        jobs_in, rem_in, _ = pc_link.pull()
        self.assertEqual((jobs_in, rem_in), (2, 1))
        data = json.loads((self.reels / "jobs.json").read_text(encoding="utf-8"))
        self.assertEqual(data["next"], 5)
        self.assertTrue(data["jobs"]["4"]["cloud"])
        self.assertIn("a tiny CLI", (Path(data["jobs"]["4"]["reel_dir"]) / "breakdown.md").read_text(encoding="utf-8"))
        self.assertEqual(data["jobs"]["2"]["status"], "saved")
        items = json.loads((self.rem / "reminders.json").read_text(encoding="utf-8"))
        self.assertEqual(items["items"]["R2"]["due"], "2030-02-01 10:00")
        self.assertEqual(items["next"], 3)
        # 4. Nothing to pull twice, and what came from the cloud isn't pushed straight back.
        self.assertEqual(pc_link.pull()[:2], (0, 0))
        self.assertEqual(pc_link.push()[0], 0)

    def test_reminder_claim_is_once(self):
        pc_link.push()
        self.assertTrue(pc_link.claim("R1", "2030-01-01 09:00"))
        self.assertFalse(pc_link.claim("R1", "2030-01-01 09:00"))
        self.assertTrue(pc_link.claim("R77", "2030-01-01 09:00"))  # not synced yet: the PC sends it

    def test_unreachable_cloud_never_blocks_reminders(self):
        (self.reels / "config.json").write_text(json.dumps({"cloud_url": "http://127.0.0.1:9"}))
        self.assertTrue(pc_link.claim("R1", "2030-01-01 09:00"))


if __name__ == "__main__":
    unittest.main()
