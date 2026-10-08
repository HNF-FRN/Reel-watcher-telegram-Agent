"""End to end with no API key and no cloud service:
  - the open engine watches a generated "reel" (commands on screen, a spoken link): ffmpeg frames, faster-whisper,
    OCR, and a local model if REEL_LLM_MODEL names one;
  - with REEL_LLM_MODEL set, an open model also builds a small script through runner.py, the same way /build N local
    does, while the test plays the user and approves each command.

Skipped unless REEL_E2E=1: it needs ffmpeg (with drawtext), espeak-ng, an OCR engine and faster-whisper (and for the
build, a model on Ollama that can call tools). CI runs it on every push and uploads what it wrote
(.github/workflows/tests.yml).
    REEL_E2E=1 python test_open_core_e2e.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REEL = ROOT / ".claude" / "skills" / "reel-watch" / "scripts" / "reel.py"
RUNNER = REEL.with_name("runner.py")
SCREENS = ["npx skills add vercel-labs/agent-skills", "Repo:\ngithub.com/HNF-FRN/Reel-watcher-telegram-Agent"]
SPEECH = ("Here are the agent skills I install on every project. "
          "Then open github dot com slash ollama slash ollama to run the models on your own computer.")
FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
         "/System/Library/Fonts/Menlo.ttc", "C:/Windows/Fonts/consola.ttf")


def make_reel(folder):
    """A 10-second video: two screens of dark text on white, and a voice over."""
    font = next((f for f in FONTS if Path(f).exists()), None)
    speech = folder / "speech.wav"
    subprocess.run(["espeak-ng", "-s", "145", "-w", str(speech), SPEECH], check=True)
    draws = []
    for i, text in enumerate(SCREENS):
        (folder / f"screen{i}.txt").write_text(text, encoding="utf-8")
        when = "lt(t,5)" if i == 0 else "gte(t,5)"
        draws.append(f"drawtext=fontfile='{font}':textfile='{folder / f'screen{i}.txt'}':fontcolor=black:fontsize=40:"
                     f"line_spacing=12:x=(w-text_w)/2:y=(h-text_h)/2:enable='{when}'")
    video = folder / "sample-reel.mp4"
    subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=white:s=1280x720:r=10:d=10", "-i", str(speech), "-vf", ",".join(draws),
                    "-t", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
    return video


@unittest.skipUnless(os.environ.get("REEL_E2E") == "1", "set REEL_E2E=1 (needs ffmpeg, espeak-ng, OCR, faster-whisper)")
class OpenEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        video = make_reel(cls.tmp)
        env = {**os.environ, "GEMINI_API_KEY": "", "GOOGLE_API_KEY": "", "REEL_HOME": str(cls.tmp / "home"),
               "PYTHONUTF8": "1"}
        r = subprocess.run([sys.executable, str(REEL), str(video), "--engine", "local",
                            "--whisper-model", os.environ.get("REEL_E2E_WHISPER", "base.en")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=1800)
        print(r.stderr[-3000:], file=sys.stderr)
        cls.code, cls.out = r.returncode, r.stdout
        m = re.search(r"^REEL_DIR: (.+)$", r.stdout, re.M)
        cls.work = Path(m.group(1).strip()) if m else None
        cls.manifest = json.loads((cls.work / "manifest.json").read_text(encoding="utf-8")) if cls.work else {}
        cls.local = (cls.work / "local.md").read_text(encoding="utf-8") if cls.work else ""
        print("\n===== local.md, written with no API key =====\n" + cls.local + "\n=====")
        if os.environ.get("REEL_E2E_OUT") and cls.work:
            out = Path(os.environ["REEL_E2E_OUT"])
            shutil.copytree(cls.work, out / cls.work.name, dirs_exist_ok=True)
            shutil.copy(video, out / video.name)
            (out / "stdout.txt").write_text(r.stdout, encoding="utf-8")

    def test_it_ran_with_the_open_engine(self):
        self.assertEqual(self.code, 0, self.out[-2000:])
        self.assertTrue(self.manifest["engine"].startswith("local ("), self.manifest["engine"])
        self.assertIsNone(self.manifest["gemini_error"])
        self.assertTrue(self.manifest["ocr"], "an OCR engine ran")

    def test_commands_and_repos_on_screen_are_found_verbatim(self):
        found = self.manifest["found"]
        self.assertIn("npx skills add vercel-labs/agent-skills", [c["value"] for c in found["commands"]])
        repos = [r["value"] for r in found["repos"]]
        self.assertIn("github.com/vercel-labs/agent-skills", repos)
        self.assertIn("github.com/HNF-FRN/Reel-watcher-telegram-Agent", repos)
        self.assertIn("npx skills add vercel-labs/agent-skills", self.local)

    def test_the_voice_over_is_transcribed(self):
        said = " ".join(s["text"] for s in self.manifest["transcript"]["segments"]).lower()
        self.assertGreaterEqual(len(said.split()), 8, said)
        self.assertRegex(said, r"skills|project|models|computer")

    def test_the_breakdown_has_every_section(self):
        for section in ("## Summary", "## Step by step", "## On-screen text", "## Tools, links and repos",
                        "## Commands shown", "## Transcript", "## Notes"):
            self.assertIn(section, self.local)
        if os.environ.get("REEL_LLM_MODEL"):
            self.assertIn(os.environ["REEL_LLM_MODEL"], self.manifest["engine"], "the local model wrote the summary")
            self.assertIn(f"written by {os.environ['REEL_LLM_MODEL']}", self.local)


def read_state(folder):
    try:
        return json.loads((folder / ".reel" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):  # being rewritten right now
        return {}


@unittest.skipUnless(os.environ.get("REEL_E2E") == "1" and os.environ.get("REEL_LLM_MODEL"),
                     "set REEL_E2E=1 and REEL_LLM_MODEL (a model on Ollama that can call tools)")
class OpenModelBuild(unittest.TestCase):
    """A build by an open model through runner.py, no Claude involved. The test plays the user: it answers every
    🔐 request with /yes, the way the phone would."""

    def test_an_open_model_builds_and_runs_a_script(self):
        home = Path(tempfile.mkdtemp())
        build = home / "builds" / "1-hello"
        (build / ".reel").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(build)], check=True)
        (build / ".reel" / "state.json").write_text(json.dumps({
            "job": 1, "kind": "build", "model": "local", "mode": "normal", "chat_id": "1", "limit_min": 20}),
            encoding="utf-8")
        task = build / ".reel" / "task.txt"
        task.write_text("Create hello.py that prints exactly: hello from an open model\n"
                        "Then run it with `python hello.py` to check that it works.", encoding="utf-8")
        env = {**os.environ, "REEL_HOME": str(home), "REEL_TG_DRYRUN": "1", "PYTHONUTF8": "1"}
        proc = subprocess.Popen([sys.executable, str(RUNNER), str(build), str(task)], env=env)
        answers, end = 0, time.time() + 1500
        answer = build / ".reel" / "answer.json"
        while proc.poll() is None and time.time() < end:
            if read_state(build).get("pending") and not answer.exists():
                tmp = answer.with_suffix(".tmp")
                tmp.write_text(json.dumps({"decision": "yes", "reason": None, "at": "e2e"}), encoding="utf-8")
                os.replace(tmp, answer)  # /yes 1
                answers += 1
            time.sleep(1)
        if proc.poll() is None:
            proc.kill()
        log = (build / ".reel" / "log.md").read_text(encoding="utf-8") if (build / ".reel" / "log.md").exists() else ""
        print(f"\n===== build log ({answers} commands approved) =====\n{log}\n=====")
        if os.environ.get("REEL_E2E_OUT"):
            out = Path(os.environ["REEL_E2E_OUT"]) / "open-model-build"
            shutil.copytree(build, out, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
        state = read_state(build)
        self.assertEqual(state.get("status"), "done", log[-3000:])
        self.assertTrue((build / "hello.py").exists(), log[-3000:])
        ran = subprocess.run([sys.executable, str(build / "hello.py")], capture_output=True, text=True, timeout=60)
        self.assertIn("hello from an open model", ran.stdout.lower())
        commits = subprocess.run(["git", "-C", str(build), "log", "--oneline"], capture_output=True, text=True).stdout
        self.assertIn("#1 run 1: build", commits)


if __name__ == "__main__":
    unittest.main()
