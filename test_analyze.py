"""Checks for analyze.py, the open engine's breakdown. OCR and the model are fakes, so this runs anywhere.
Run: python test_analyze.py"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / ".claude" / "skills" / "reel-watch" / "scripts"))
import analyze  # noqa: E402
import llm  # noqa: E402
import ocr  # noqa: E402

SCREENS = {"f1.jpg": ["$ npx skills add vercel-labs/agent-skills"],
           "f2.jpg": ["$ npx skills add vercel-labs/agent-skills"],  # same screen, still showing
           "f3.jpg": ["Open github.com/HNF-FRN/Reel-watcher-telegram-Agent"]}
TRANSCRIPT = {"language": "en", "segments": [{"start": 0.5, "end": 2.0, "text": "Install the skills with one command."}]}
META = {"description": "3 skills you need. Comment SKILLS for the list"}
CFG = {"url": "http://models.local:11434/v1", "model": "qwen2.5vl:7b", "key": None}


def frames():
    return [{"t": 1.0, "path": "f1.jpg"}, {"t": 3.0, "path": "f2.jpg"}, {"t": 5.0, "path": "f3.jpg"}]


class Breakdown(unittest.TestCase):
    def setUp(self):
        fake = mock.patch.object(ocr, "_engine", ("fakeocr", lambda p: SCREENS[Path(p).name]))
        fake.start()
        self.addCleanup(fake.stop)

    def run_it(self, chat=None, cfg=CFG):
        with mock.patch.object(llm, "resolve", lambda model=None: cfg), \
                mock.patch.object(llm, "chat", chat or (lambda *a, **k: self.fail("no model expected"))), \
                mock.patch.object(llm, "image_part", lambda p: {"type": "image_url", "image_url": {"url": "data:,"}}):
            return analyze.analyze("video", frames(), TRANSCRIPT, META, duration=6, whisper="small")

    def section(self, md, title):
        return md.split(f"## {title}\n", 1)[1].split("\n## ", 1)[0].strip()

    def test_without_a_model_the_breakdown_is_still_complete(self):
        md, info = self.run_it(cfg=None)
        self.assertEqual([line for line in md.splitlines() if line.startswith("## ")],
                         ["## Summary", "## Step by step", "## On-screen text (verbatim, read by OCR)",
                          "## Tools, links and repos", "## Commands shown", "## Transcript", "## Notes"])
        self.assertEqual(self.section(md, "Summary"), "3 skills you need. Comment SKILLS for the list")
        self.assertEqual(self.section(md, "Step by step").splitlines(), [
            "- [0:00] said: Install the skills with one command.",
            "- [0:01] on screen: `$ npx skills add vercel-labs/agent-skills`",
            "- [0:05] on screen: `Open github.com/HNF-FRN/Reel-watcher-telegram-Agent`"])
        screen = self.section(md, "On-screen text (verbatim, read by OCR)")
        self.assertEqual(screen.count("npx skills add"), 1, "a screen that stays up is shown once")
        self.assertIn("`npx skills add vercel-labs/agent-skills` (screen 0:01, screen 0:03)",
                      self.section(md, "Commands shown"))
        found = self.section(md, "Tools, links and repos")
        self.assertIn("`github.com/vercel-labs/agent-skills`", found)
        self.assertIn("`github.com/HNF-FRN/Reel-watcher-telegram-Agent`", found)
        self.assertEqual(self.section(md, "Transcript"), "[0:00] Install the skills with one command.")
        notes = self.section(md, "Notes")
        self.assertIn("comment “SKILLS” (caption)", notes)
        self.assertIn("No model answered", notes)
        self.assertEqual((info["ocr"], info["llm"], info["whisper"]), ("fakeocr", None, "small"))
        self.assertEqual(analyze.label(info), "local (whisper small + fakeocr)")

    def test_a_local_model_writes_the_summary_but_never_the_quotes(self):
        sent = []

        def chat(messages, cfg, **kw):
            sent.append(messages)
            return {"role": "assistant", "content": "<think>plan</think>## Summary\nA demo of agent skills.\n\n"
                                                    "## Step by step\n- [0:01] Runs `npx skills add`"}
        md, info = self.run_it(chat)
        self.assertEqual(self.section(md, "Summary"), "A demo of agent skills.")
        self.assertEqual(self.section(md, "Step by step"), "- [0:01] Runs `npx skills add`")
        self.assertIn("$ npx skills add vercel-labs/agent-skills", md, "verbatim text still comes from OCR")
        user = sent[0][1]["content"]
        self.assertIn("[0:01] on screen: $ npx skills add vercel-labs/agent-skills\n[0:05] on screen: Open",
                      user[0]["text"], "the model gets the screens in time order (the repeat at 0:03 is skipped)")
        self.assertIn("[0:00] said: Install the skills", user[0]["text"])
        self.assertEqual(len(user), 4, "a vision model sees the frames too")
        self.assertEqual((info["llm"], info["images_seen"]), ("qwen2.5vl:7b", 3))
        self.assertIn("written by qwen2.5vl:7b at models.local:11434, which saw 3 frames", md)
        self.assertEqual(analyze.label(info), "local (whisper small + fakeocr + qwen2.5vl:7b)")

    def test_text_only_models_get_no_images(self):
        sent = []
        chat = lambda messages, cfg, **kw: sent.append(messages) or {"content": "## Summary\nX\n## Step by step\n- y"}
        with mock.patch.dict(os.environ, {"REEL_LLM_VISION": ""}):
            self.run_it(chat, cfg={**CFG, "model": "qwen3:8b"})
        self.assertIsInstance(sent[0][1]["content"], str)

    def test_a_looping_model_is_not_trusted(self):
        # what qwen2.5:1.5b wrote in CI: the same evidence line, nested deeper on every line
        loop = "\n".join("- [0:05] " + "said: '" * i + "Repo: | github. com/HNF -FRN/x'" for i in range(2, 30))
        md, info = self.run_it(lambda *a, **k: {"content": f"## Summary\nA demo of skills.\n\n## Step by step\n{loop}"})
        self.assertEqual(self.section(md, "Summary"), "A demo of skills.")
        self.assertIn("- [0:01] on screen:", self.section(md, "Step by step"), "the timeline replaces the loop")
        self.assertNotIn("said: 'said:", md)
        self.assertIn("Its steps were unusable", self.section(md, "Notes"))
        self.assertEqual(info["llm"], "qwen2.5vl:7b")

    def test_a_model_that_only_pastes_the_input_is_not_used(self):
        md, info = self.run_it(lambda *a, **k: {"content": "SPOKEN: x\nSPOKEN: y\nSPOKEN: z"})
        self.assertEqual(self.section(md, "Summary"), "3 skills you need. Comment SKILLS for the list")
        self.assertIn("gave no usable answer", self.section(md, "Notes"))
        self.assertIsNone(info["llm"])

    def test_a_failing_model_falls_back_to_the_timeline(self):
        def chat(*a, **k):
            raise llm.LLMError("boom")
        md, info = self.run_it(chat)
        self.assertIn("- [0:01] on screen:", md)
        self.assertIn("The local model failed (boom)", md)
        self.assertIsNone(info["llm"])

    def test_without_ocr(self):
        with mock.patch.object(ocr, "_engine", (None, None)), mock.patch.dict(os.environ, {"REEL_LLM": "off"}):
            md, info = analyze.analyze("video", frames(), None, {}, whisper="small")
        self.assertIn("(not read: no OCR engine is installed", md)
        self.assertIn("pip install rapidocr onnxruntime", md)
        self.assertIsNone(info["ocr"])


class Parsing(unittest.TestCase):
    def test_model_output_variants(self):
        self.assertEqual(analyze.split_sections("## Summary\nOne line.\n\n## Step by step\n- a\n- b\n## Extra\nz"),
                         ("One line.", "- a\n- b"))
        self.assertEqual(analyze.split_sections("**Summary**: no headings here\n- step"),
                         ("**Summary**: no headings here", "- step"))
        self.assertEqual(analyze.split_sections("# Summary:\n- A thing\n# Steps\n1. x"), ("A thing", "1. x"))

    def test_where_labels(self):
        self.assertEqual(analyze.where({"t": 65.2, "path": "x"}, 1), "screen 1:05")
        self.assertEqual(analyze.where({"t": None, "path": "x"}, 2), "image 2")
        self.assertEqual(analyze.where({"t": None, "path": "x", "slide": 3}, 1), "slide 3")
        self.assertEqual(analyze.where({"t": 1.5, "path": "x", "slide": 4}, 1), "slide 4, 0:01")

    def test_summary_of_reads_ours_and_geminis(self):
        self.assertEqual(analyze.summary_of("## Summary\nA tool demo.\n\n## Step by step\n- x"), "A tool demo.")
        self.assertEqual(analyze.summary_of("# Reel\n## Summary\n- Bulleted summary"), "Bulleted summary")

    def test_frames_spread_over_the_video(self):
        fs = [{"path": str(i)} for i in range(10)]
        self.assertEqual([f["path"] for f in analyze.pick_frames(fs, 4)], ["0", "3", "6", "9"])


if __name__ == "__main__":
    unittest.main()
