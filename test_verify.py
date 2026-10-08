"""Checks for verify.py (/deeper N): the registries are a fake table of answers, so this needs no network.
Run: python test_verify.py"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / ".claude" / "skills" / "reel-watch" / "scripts"))
import verify  # noqa: E402

ANSWERS = {
    "https://api.github.com/repos/vercel-labs/agent-skills": (200, {
        "full_name": "vercel-labs/agent-skills", "stargazers_count": 12345, "license": {"spdx_id": "MIT"},
        "pushed_at": "2026-09-30T10:00:00Z", "archived": False, "description": "Skills for agents",
        "html_url": "https://github.com/vercel-labs/agent-skills"}),
    "https://api.github.com/repos/old/tool": (200, {
        "full_name": "old/tool", "stargazers_count": 40, "license": None, "pushed_at": "2020-01-01T00:00:00Z",
        "archived": True}),
    "https://api.github.com/repos/made/up": (404, None),
    "https://registry.npmjs.org/skills/latest": (200, {"version": "1.4.0", "license": "MIT", "description": "CLI"}),
    "https://api.npmjs.org/downloads/point/last-week/skills": (200, {"downloads": 52000}),
    "https://registry.npmjs.org/@scope%2Fpkg/latest": (200, {"version": "0.1.0", "deprecated": "use x"}),
    "https://api.npmjs.org/downloads/point/last-week/@scope/pkg": (200, {"downloads": 3}),
    "https://pypi.org/pypi/not-a-package/json": (404, None),
}


def fake_get(url, timeout=15):
    return ANSWERS.get(url, (None, "unreachable in this test"))


def found(repos=(), packages=()):
    return {"repos": [{"value": r, "where": ["screen 0:01"]} for r in repos],
            "packages": [{"value": p, "where": ["screen 0:01"]} for p in packages]}


class Verify(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(verify, "get", fake_get)
        patch.start()
        self.addCleanup(patch.stop)

    def test_what_exists_and_what_does_not(self):
        results = dict(verify.check(found(["github.com/vercel-labs/agent-skills", "github.com/made/up"],
                                          ["npm: skills", "pypi: not-a-package", "brew: ffmpeg"])))
        self.assertEqual(list(results), ["github.com/vercel-labs/agent-skills", "github.com/made/up", "npm: skills",
                                         "pypi: not-a-package"], "brew isn't checked")
        self.assertEqual(results["github.com/vercel-labs/agent-skills"]["facts"], ["★ 12.3k", "MIT", "updated 2026-09-30"])
        self.assertIs(results["github.com/made/up"]["ok"], False)
        self.assertEqual(results["npm: skills"]["facts"], ["v1.4.0", "52k downloads a week", "MIT"])
        self.assertEqual(results["pypi: not-a-package"]["why"], "not on PyPI")
        card = verify.card(4, list(results.items()))
        self.assertIn("🔎 **#4 · 2 of 4 not found: be careful**", card)
        self.assertIn("- ✅ `github.com/vercel-labs/agent-skills`: ★ 12.3k · MIT · updated 2026-09-30", card)
        self.assertIn("- ❌ `github.com/made/up`: not on GitHub", card)
        self.assertIn("/plan 4  plan it", card)

    def test_warnings(self):
        results = verify.check(found(["github.com/old/tool"], ["npm: @scope/pkg"]))
        self.assertEqual([r["warn"] for _, r in results], ["archived", "deprecated"])
        self.assertIn("no license", results[0][1]["facts"])
        self.assertEqual(verify.verdict(results), "it all exists, 2 with a warning")
        self.assertTrue(verify.line(*results[0]).startswith("⚠️ `github.com/old/tool`: ★ 40 · no license"))

    def test_unreachable_and_empty(self):
        results = verify.check(found(["github.com/some/where"]))
        self.assertIsNone(results[0][1]["ok"])
        self.assertIn("couldn't check", results[0][1]["why"])
        self.assertEqual(verify.verdict(results), "couldn't reach the registries, try later")
        self.assertEqual(verify.verdict([]), "nothing to check: it names no repo or package")
        self.assertNotIn("**Checked**", verify.card(7, []))

    def test_research_md_keeps_the_links(self):
        md = verify.research_md(4, verify.check(found(["github.com/vercel-labs/agent-skills"])))
        self.assertIn("## github.com/vercel-labs/agent-skills", md)
        self.assertIn("- Link: https://github.com/vercel-labs/agent-skills", md)
        self.assertIn("- About: Skills for agents", md)

    def test_numbers(self):
        self.assertEqual([verify.short(n) for n in (7, 1000, 1234, 2_500_000)], ["7", "1k", "1.2k", "2.5M"])


if __name__ == "__main__":
    unittest.main()
