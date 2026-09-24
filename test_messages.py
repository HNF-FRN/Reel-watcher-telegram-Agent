"""Checks for tgfmt.py, the formatter every Telegram message goes through. Run: python test_messages.py"""
import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / ".claude" / "skills" / "reel-watch" / "scripts"))
import tgfmt  # noqa: E402

ALLOWED = {"b", "i", "code", "pre", "a", "blockquote"}


class Balance(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.bad = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            self.bad.append(tag)
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.bad.append("/" + tag)


def valid(html):
    p = Balance()
    p.feed(html)
    return not p.bad and not p.stack


class Commands(unittest.TestCase):
    def test_commands_become_one_tap(self):
        self.assertEqual(tgfmt.tapify("/plan 5 · /build 5 · /save 5"), "/plan_5 · /build_5 · /save_5")
        self.assertEqual(tgfmt.tapify("/snooze R3 1h or /done R3"), "/snooze_R3_1h or /done_R3")
        self.assertEqual(tgfmt.tapify("/build 4 opus to redo"), "/build_4_opus to redo")
        self.assertEqual(tgfmt.tapify("(/plan #4)"), "(/plan_4)")
        self.assertEqual(tgfmt.tapify("/r 7 full"), "/r_7_full")

    def test_free_text_commands_and_placeholders_stay(self):
        for text in ("/tell 4 <changes>", "/remind tomorrow 9:00 call", "/plan N", "/new idea", "see /telegram:access pair"):
            self.assertEqual(tgfmt.tapify(text), text)
        self.assertEqual(tgfmt.tapify("/tell 4"), "/tell 4")

    def test_tapped_commands_read_back(self):
        self.assertEqual(tgfmt.command_words("/snooze_R3_1h"), "/snooze R3 1h")
        self.assertEqual(tgfmt.command_words("/build_4_opus"), "/build 4 opus")
        self.assertEqual(tgfmt.command_words("/plan 4"), "/plan 4")
        self.assertEqual(tgfmt.command_words("/some_thing"), "/some_thing")


class Html(unittest.TestCase):
    def test_markdown_becomes_telegram_html(self):
        out = tgfmt.to_html("# Title\n**Bold** and *it* and `a<b>`\n- one\n  - two\n> quoted\n[docs](https://x.dev/a?b=1&c=2)")
        self.assertIn("<b>Title</b>", out)
        self.assertIn("<b>Bold</b>", out)
        self.assertIn("<i>it</i>", out)
        self.assertIn("<code>a&lt;b&gt;</code>", out)
        self.assertIn("• one\n  • two", out)
        self.assertIn("<blockquote>quoted</blockquote>", out)
        self.assertIn('<a href="https://x.dev/a?b=1&amp;c=2">docs</a>', out)
        self.assertTrue(valid(out))

    def test_code_is_left_alone(self):
        out = tgfmt.to_html("```\nnpm i **x** /plan 4 <y>\n```\nrun `/plan 4` or /plan 4")
        self.assertIn("<pre>npm i **x** /plan 4 &lt;y&gt;\n</pre>".replace("\n</pre>", "</pre>"), out)
        self.assertIn("<code>/plan 4</code> or /plan_4", out)

    def test_snake_case_and_stars_in_prose(self):
        out = tgfmt.to_html("set compact_at_percent = 25 * 2, 6.3K stars; a*b*c")
        self.assertNotIn("<i>", out)
        self.assertIn("compact_at_percent", out)

    def test_real_messages_stay_valid(self):
        samples = [
            '#4: **Jev** is TypeSafe AI\'s model — not text.\n\n**Build vs Buy:**\n- **Cheapest**: RB2B ($129/mo)\n\n'
            'Full research attached. **`/plan 4`** to explore; "4 3" to dig deeper.',
            "| Tool | Cost |\n|---|---|\n| RB2B | $129 |\n",
            "**a *b** c*", "unclosed ``` fence\nnpm i", "<script>alert(1)</script> & co",
        ]
        for s in samples:
            self.assertTrue(valid(tgfmt.to_html(s)), s)

    def test_long_text_splits_outside_code(self):
        text = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(40)) + "\n\n```\n" + "x\n" * 50 + "```"
        parts = tgfmt.chunks(text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(tgfmt.to_html(p)) < 4096 for p in parts))
        self.assertTrue(all(p.count("```") % 2 == 0 for p in parts))

    def test_plain_fallback(self):
        self.assertEqual(tgfmt.plain("**Hi** `x`\n- a\n/yes 4"), "Hi x\n• a\n/yes_4")


if __name__ == "__main__":
    unittest.main()
