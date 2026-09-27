"""Checks for the shared helpers in common.py. Run: python test_common.py   (temp folders only)"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / ".claude" / "skills" / "reel-watch" / "scripts"))
import common  # noqa: E402


class Pacific(unittest.TestCase):
    def test_fallback_follows_daylight_saving(self):
        # without tzdata (a bare Windows Python) zoneinfo fails and the US rule is used
        with mock.patch.dict(sys.modules, {"zoneinfo": None}):
            at = lambda *a: common.pacific_now(datetime(*a, tzinfo=timezone.utc)).strftime("%m-%d %H:%M")
            self.assertEqual(at(2026, 7, 1, 7, 30), "07-01 00:30")   # summer: UTC-7
            self.assertEqual(at(2026, 1, 15, 7, 30), "01-14 23:30")  # winter: UTC-8
            self.assertEqual(at(2026, 3, 8, 9, 59), "03-08 01:59")   # just before 2:00 PST, 2nd Sunday of March
            self.assertEqual(at(2026, 3, 8, 10, 0), "03-08 03:00")   # clocks jump forward
            self.assertEqual(at(2026, 11, 1, 8, 59), "11-01 01:59")  # 1st Sunday of November, still PDT
            self.assertEqual(at(2026, 11, 1, 9, 0), "11-01 01:00")   # clocks fall back


class Store(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "jobs.json"

    def test_round_trip_and_lock_released(self):
        with common.locked_json(self.path, {"next": 1}) as data:
            data["next"] = 2
        with common.locked_json(self.path, {"next": 1}, write=False) as data:
            self.assertEqual(data, {"next": 2})
        self.assertFalse(Path(str(self.path) + ".lock").exists())

    def test_corrupt_file_is_never_overwritten(self):
        self.path.write_text("{half written", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            with common.locked_json(self.path, {"next": 1}):
                pass
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{half written")

    def test_stale_lock_from_a_crashed_run_is_broken(self):
        lock = Path(str(self.path) + ".lock")
        lock.write_text("")
        os.utime(lock, (0, common.time.time() - 60))  # left behind a minute ago, owner unknown
        with common.locked_json(self.path, {}):
            pass
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
        lock.write_text(f"{dead.stdout.strip()}:x")  # fresh, but its owner has exited
        with common.locked_json(self.path, {}):
            pass

    def test_live_holder_is_never_broken_or_released_by_another(self):
        lock = Path(str(self.path) + ".lock")
        with common.file_lock(self.path):
            os.utime(lock, (0, common.time.time() - 600))  # a slow holder: old, but its process is alive
            with self.assertRaises(TimeoutError):
                with common.file_lock(self.path, stale_sec=0.2):
                    pass
            self.assertTrue(lock.exists())
            lock.write_text("123:someone-else")  # e.g. broken and re-taken while we were slow
        self.assertEqual(lock.read_text(), "123:someone-else", "only the owner removes its lock")


class Config(unittest.TestCase):
    def test_saving_settings_keeps_cloud_url(self):
        cfg_file = Path(tempfile.mkdtemp()) / "config.json"
        cfg_file.write_text(json.dumps({"cloud_url": "https://x.workers.dev", "models": {"watch": "haiku"}}))
        with mock.patch.object(common, "CONFIG", cfg_file):
            cfg = common.load_config()
            self.assertEqual((cfg["models"]["watch"], cfg["models"]["plan"], cfg["mode"]), ("haiku", "opus", "normal"))
            cfg["models"]["build"] = "opus"
            common.write_json(cfg_file, cfg)
            self.assertEqual(common.load_config()["cloud_url"], "https://x.workers.dev")


if __name__ == "__main__":
    unittest.main()
