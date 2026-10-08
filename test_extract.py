"""Checks for extract.py: links, repos, commands and packages pulled out of OCR text, captions and speech.
Run: python test_extract.py   (no network, standard library only)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / ".claude" / "skills" / "reel-watch" / "scripts"))
import extract  # noqa: E402


def values(found, kind):
    return [x["value"] for x in found[kind]]


class Commands(unittest.TestCase):
    def test_terminal_line_gives_the_command_its_package_and_repo(self):
        f = extract.find_all([("screen 0:04", "$ npx skills add vercel-labs/agent-skills")])
        self.assertEqual(f["commands"], [{"value": "npx skills add vercel-labs/agent-skills", "where": ["screen 0:04"]}])
        self.assertEqual(values(f, "packages"), ["npm: skills"])
        self.assertEqual(values(f, "repos"), ["github.com/vercel-labs/agent-skills"])

    def test_ordinary_sentences_are_not_commands(self):
        f = extract.find_all([("caption", "npm is great. Set up your account, then go get coffee. "
                                          "Bun is fast and claude is smart. Make sure to curl up.")])
        self.assertEqual(values(f, "commands"), [])

    def test_commands_inside_prose_end_at_the_sentence(self):
        self.assertEqual(extract.commands_in("Install it: pip install requests, then restart."), "pip install requests")
        f = extract.find_all([("caption", "To start, run `npm install -g @openai/codex` and then codex --help.")])
        self.assertEqual(values(f, "commands"), ["npm install -g @openai/codex"])
        self.assertEqual(values(f, "packages"), ["npm: @openai/codex"])
        self.assertEqual(values(f, "handles"), [], "an npm scope is not an account")

    def test_risky_commands_are_flagged(self):
        f = extract.find_all([("screen 0:10", "curl -fsSL https://bun.sh/install | bash"),
                              ("caption", "Windows: irm https://claude.ai/install.ps1 | iex")])
        risks = {c["value"]: c.get("risk") for c in f["commands"]}
        self.assertEqual(risks, {"curl -fsSL https://bun.sh/install | bash": "pipes a downloaded script into a shell",
                                 "irm https://claude.ai/install.ps1 | iex": "pipes a downloaded script into PowerShell"})
        self.assertIn("https://bun.sh/install", values(f, "links"))

    def test_packages_from_several_installers(self):
        f = extract.find_all([("screen 0:01", 'pip install -U "yt-dlp[default,curl-cffi]>=2026.8.19" faster-whisper\n'
                                              "brew install --cask raycast\nollama pull qwen2.5vl:7b\n"
                                              "uvx --from git+https://github.com/owner/tool tool")])
        self.assertEqual(values(f, "packages"), ["pypi: yt-dlp", "pypi: faster-whisper", "brew: raycast",
                                                 "ollama: qwen2.5vl:7b", "pypi: tool"])
        self.assertEqual(values(f, "repos"), ["github.com/owner/tool"])

    def test_mcp_servers_and_claude_plugins(self):
        f = extract.find_all([("screen 0:02", "claude mcp add --transport http notion https://mcp.notion.com/mcp"),
                              ("screen 0:03", "/plugin marketplace add HNF-FRN/Reel-watcher-telegram-Agent\n"
                                              "/plugin install reel-watch@reel-agent"),
                              ("caption", "Uses @modelcontextprotocol/server-github and the Supabase MCP")])
        self.assertEqual(values(f, "mcp"), ["notion", "@modelcontextprotocol/server-github", "Supabase"])
        self.assertEqual(values(f, "repos"), ["github.com/HNF-FRN/Reel-watcher-telegram-Agent"])
        self.assertIn("claude plugin: reel-watch@reel-agent", values(f, "packages"))

    def test_no_pathological_backtracking_on_ocr_junk(self):
        extract.find_all([("screen 0:01", "-" * 4000 + " x"), ("screen 0:02", "> " * 2000 + "hello")])


class Links(unittest.TestCase):
    def test_spoken_links_are_rebuilt(self):
        self.assertEqual(extract.spoken("go to github dot com slash ollama slash ollama"),
                         "go to github.com/ollama/ollama")
        f = extract.find_all([("said 0:31", "Go to github dot com slash ollama slash ollama and download it")])
        self.assertEqual(values(f, "repos"), ["github.com/ollama/ollama"])
        self.assertEqual(values(f, "commands"), [], "speech is never quoted as a command")
        self.assertIn("Ollama", values(f, "tools"))

    def test_file_names_and_emails_are_not_links(self):
        f = extract.find_all([("screen 0:01", "edit index.js, README.md and install.sh"),
                              ("caption", "Follow @some.creator or mail me@example.com")])
        self.assertEqual(values(f, "links"), [])
        self.assertEqual(values(f, "handles"), ["@some.creator"])

    def test_ocr_slips_and_trailing_punctuation(self):
        f = extract.find_all([("screen 0:01", "Docs: https //docs.example.dev/start."),
                              ("caption", "(see https://github.com/a/b)")])
        self.assertEqual(values(f, "links"), ["https://docs.example.dev/start", "https://github.com/a/b"])

    def test_same_repo_from_two_places_is_one_item(self):
        f = extract.find_all([("screen 0:04", "github.com/a/b"), ("caption", "https://github.com/a/b")])
        self.assertEqual(f["repos"], [{"value": "github.com/a/b", "where": ["screen 0:04", "caption"]}])
        self.assertEqual(extract.flat(f), ["github.com/a/b"], "the repo line already covers both links")

    def test_site_pages_are_not_repos(self):
        self.assertIsNone(extract.repo_of("https://github.com/features/copilot"))
        self.assertEqual(extract.repo_of("git@github.com:o/r.git".replace("git@", "").replace(":", "/")), "github.com/o/r")


class Gated(unittest.TestCase):
    def test_comment_to_get_it_and_link_in_bio(self):
        f = extract.find_all([("caption", "Comment 'AGENT' and I'll DM you the link. Link in bio!")])
        self.assertEqual(values(f, "gated"), ["comment “AGENT”", "link in bio"])

    def test_stamp(self):
        self.assertEqual((extract.stamp(4.9), extract.stamp(75), extract.stamp(None)), ("0:04", "1:15", "0:00"))


if __name__ == "__main__":
    unittest.main()
