"""extract.py - pull the exact things a viewer would act on out of a video's text: links, repos, commands,
packages, MCP servers, accounts and gated offers ("comment AGENT and I'll send it").

Standard library only and no model involved, so everything it reports was really on screen, in the caption or
said. Each item keeps where it was seen ("caption", "screen 0:04", "said 0:31", "slide 2"), so it can be checked
against the frame. The open engine (analyze.py) builds the breakdown from it; reelbot.py builds its reply card.

    found = find_all([("caption", text), ("screen 0:04", ocr_text), ("said 0:31", words)])
    found["commands"] -> [{"value": "npx skills add owner/repo", "where": ["screen 0:04"]}, ...]
"""
import re
import shlex

# Top-level domains recognised without "https://" in front. Deliberately short: "install.sh", "index.js" or
# "README.md" must not turn into links.
TLDS = ("com|io|ai|dev|app|co|org|net|so|gg|xyz|me|tech|tools|run|cloud|studio|site|page|land|tv|fm|ly|pro|"
        "info|chat|email|new|link|build|codes|works|software|systems|space|store|shop|club|online|live|to")
URL_RE = re.compile(r"\bhttps?://[^\s<>\"'`]+", re.I)
DOMAIN_RE = re.compile(rf"(?<![\w@./:-])((?:[a-z0-9](?:[a-z0-9-]{{0,61}}[a-z0-9])?\.)+(?:{TLDS}))(?![\w-])"
                       r"(/[^\s<>\"'`]*)?", re.I)
REPO_HOSTS = ("github.com", "gitlab.com", "huggingface.co", "codeberg.org")
NOT_OWNERS = {"features", "about", "pricing", "orgs", "topics", "marketplace", "sponsors", "settings", "login",
              "apps", "collections", "trending", "explore", "search", "notifications", "new", "join", "docs",
              "enterprise", "security", "customer-stories", "readme", "models", "datasets", "spaces", "blog"}
SPOKEN_TLDS = "com|io|ai|dev|app|co|org|net|sh|so|gg|xyz|me"

# Something a viewer would type. Ambiguous words ("go", "bun", "claude", "set", "curl") only count with the
# sub-command, flag or argument that makes them a command, so "npm is great" or "set up your account" don't.
COMMANDS = [
    r"npx\s+\S", r"npm\s+(?:i|install|add|create|init|exec|x|run|ci|link|start|test|uninstall)\b",
    r"pnpm\s+(?:add|i|install|dlx|create|run|exec)\b", r"pnpx\s+\S", r"yarn\s+(?:add|global|create|dlx)\b",
    r"bunx\s+\S", r"bun\s+(?:add|i|install|create|x|run)\b", r"deno\s+(?:run|install|task|add)\b",
    r"pip3?\s+install\b", r"pipx\s+(?:install|run)\b", r"uvx\s+\S", r"uv\s+(?:pip|add|tool|run|sync|init|venv)\b",
    r"python3?\s+(?:-m\s+\S|\S+\.py\b)", r"py\s+-m\s+\S", r"conda\s+(?:install|create)\b",
    r"brew\s+(?:install|tap|upgrade)\b", r"apt(?:-get)?\s+install\b", r"(?:winget|choco|scoop)\s+install\b",
    r"git\s+clone\b", r"gh\s+(?:repo|extension|copilot)\b", r"(?:curl|wget)\s+(?:-|['\"]?https?://)",
    r"(?:irm|iwr)\s+\S", r"docker\s+(?:run|compose|pull|build|exec)\b", r"docker-compose\s+up\b",
    r"podman\s+(?:run|pull)\b", r"ollama\s+(?:run|pull|serve|create|launch)\b",
    r"claude\s+(?:mcp|plugin|config|update|install|doctor|-p\b|--)", r"codex\s+(?:exec|mcp|--)",
    r"gemini\s+(?:mcp|extensions|-p\b|--)", r"opencode\s+(?:run|serve|auth|--)", r"aider\s+--",
    r"openclaw\s+(?:skills|install|onboard|gateway|--)", r"clawhub\s+(?:install|search|--)",
    r"cargo\s+(?:install|add|run)\b", r"go\s+(?:install|run|get)\s+\S*[./]\w",
    r"composer\s+(?:require|create-project)\b", r"gem\s+install\b", r"dotnet\s+tool\s+install\b",
    r"(?:code|cursor)\s+--install-extension\b", r"wrangler\s+(?:deploy|dev|init|login|d1|kv|r2|pages|secret)\b", r"vercel\s+(?:deploy|dev|link|env|login|--prod)",
    r"netlify\s+(?:deploy|dev|init|login)\b", r"supabase\s+(?:init|start|link|db|functions|login|gen)\b",
    r"firebase\s+(?:deploy|init|login|emulators)\b", r"(?:kubectl\s+(?:apply|create|run)|helm\s+(?:install|repo)|"
    r"terraform\s+(?:init|apply))\b", r"(?-i:export\s+[A-Z_][A-Z0-9_]*=)", r"(?-i:setx?\s+[A-Z_][A-Z0-9_]*[ =])",
    r"/(?:plugin|mcp|agents|hooks|install-github-app|skills)\b",
]
# A command starts a line, or follows a prompt sign, a space, a quote or a colon.
COMMAND_RE = re.compile(r"(?:^|(?<=[\s>$%#❯➜→•*·`'\"(:]))(?P<cmd>(?:sudo\s+)?(?:" + "|".join(COMMANDS) +
                        r")[^\n`]*)", re.I)
PROSE_END = re.compile(r"\s+(?:to|and|then|so|which|for|if|in order to|or)\s+[a-z]|[.,;!?](?:\s|$)")
RISKS = [
    (re.compile(r"\b(?:curl|wget)\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b", re.I), "pipes a downloaded script into a shell"),
    (re.compile(r"\b(?:irm|iwr|invoke-webrequest|invoke-restmethod)\b[^|\n]*\|\s*iex\b", re.I),
     "pipes a downloaded script into PowerShell"),
    (re.compile(r"--dangerously|--yolo\b|--no-sandbox\b|bypassPermissions", re.I), "turns off safety prompts"),
    (re.compile(r"\brm\s+-rf?\s+[/~*]", re.I), "deletes files"),
    (re.compile(r"\bchmod\s+(?:-R\s+)?777\b", re.I), "opens file permissions wide"),
    (re.compile(r"^\s*sudo\b", re.I), "runs as administrator"),
]
MCP_RE = [re.compile(p) for p in (
    r"@modelcontextprotocol/server-[\w-]+", r"\bmcp-server-[\w-]+", r"\b[\w-]+-mcp(?:-server)?\b",
    r"\b([A-Z][\w.-]+(?: [A-Z][\w.-]+)?) MCP\b")]
NOT_MCP = {"the", "an", "a", "this", "your", "my", "add", "use", "install", "build", "connect", "with", "new", "best",
           "free", "top", "any", "every", "one", "remote", "local", "custom", "official", "first"}
GATED = [
    (re.compile(r"\b(?i:comment)(?:\s+(?i:the\s+word))?\s+[\"“'‘«]([^\"”'’»\n]{1,30})[\"”'’»]"), "comment “{}”"),
    (re.compile(r"\b(?i:comment)(?:\s+(?i:the\s+word))?\s+([A-Z0-9][A-Z0-9-]{1,24})\b"), "comment “{}”"),
    (re.compile(r"\b(?:DM|dm)\s+(?:me\s+)?[\"“'‘]?([A-Z0-9][A-Z0-9-]{1,24})\b"), "DM “{}”"),
    (re.compile(r"\blink (?:is )?in (?:my |the )?bio\b", re.I), "link in bio"),
    (re.compile(r"\b(?:DM|message) me\b", re.I), "DM me"),
]
HANDLE_RE = re.compile(r"(?<![\w@./])@([A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?)(?![\w/@])")
# Products that matter when someone wants to rebuild a workflow. The lower-case set matches in any case; the
# rest only as written, because "Cursor", "Make" or "Bolt" are ordinary words otherwise.
TOOLS = ["Claude Code", "Claude", "ChatGPT", "OpenAI", "Codex", "Gemini", "Google AI Studio", "NotebookLM",
         "Perplexity", "Cursor", "Windsurf", "GitHub Copilot", "Copilot", "Lovable", "Bolt.new", "v0", "Replit",
         "n8n", "Zapier", "Make.com", "Ollama", "LM Studio", "OpenRouter", "Hugging Face", "Supabase", "Firebase",
         "Vercel", "Netlify", "Cloudflare", "Notion", "Obsidian", "Airtable", "Raycast", "Docker", "LangChain",
         "LangGraph", "CrewAI", "AutoGen", "LlamaIndex", "Dify", "Flowise", "OpenClaw", "MCP", "Midjourney",
         "Runway", "ElevenLabs", "Suno", "HeyGen", "CapCut", "Canva", "Figma", "Framer", "Webflow", "Stripe",
         "Telegram", "Discord", "Slack", "WhatsApp", "Gmail", "Google Sheets", "Python", "Node.js", "Next.js",
         "Tailwind", "Playwright", "Puppeteer", "Selenium", "Apify", "Browserbase", "Firecrawl", "Tavily", "Grok",
         "DeepSeek", "Qwen", "Llama", "Mistral", "Groq", "Whisper"]
LOWER_OK = {"n8n", "ollama", "chatgpt", "supabase", "langchain", "langgraph", "crewai", "llamaindex", "openrouter",
            "elevenlabs", "midjourney", "firecrawl", "playwright", "puppeteer", "openclaw", "notebooklm", "deepseek"}
TOOL_RES = [(t, re.compile(rf"(?<![\w.-]){re.escape(t)}(?![\w-])", re.I if t.lower() in LOWER_OK else 0))
            for t in TOOLS]
REPO_ARG = re.compile(r"^[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*$")
PACKAGE = re.compile(r"^(?:@[\w.-]+/)?[\w.-]+$")
SPACED_LINK = re.compile(rf"(?<![\w-])[\w-]+ ?\. ?(?:{TLDS}) ?/(?:[^\s/]+| (?=[-./_]\w)|(?<=[-./_]) (?=\w)|/)*", re.I)
SCREEN = ("screen", "image", "slide")


def stamp(seconds):
    """12.4 -> "0:12"."""
    s = int(seconds or 0)
    return f"{s // 60}:{s % 60:02d}"


def spoken(text):
    """Whisper writes what it hears: "github dot com slash ollama" -> "github.com/ollama"."""
    t = re.sub(rf"\b([a-z0-9-]+)\s+dot\s+({SPOKEN_TLDS})\b", r"\1.\2", text, flags=re.I)
    for _ in range(4):  # one path segment per round
        t = re.sub(rf"(\b[\w-]+\.(?:{SPOKEN_TLDS})(?:/[\w.-]+)*)\s+slash\s+([\w.-]+)", r"\1/\2", t, flags=re.I)
    return t


def ocr_fixes(text, screen=False):
    """Typical OCR slips that break links: "https //x" and "https: //x". On screen text (screen=True), also the
    stray spaces OCR puts into links set in monospace fonts: "github. com/HNF -FRN/x" -> "github.com/HNF-FRN/x".
    Only a space touching one side of . / - _ inside a link goes, so " - " in a sentence stays."""
    text = re.sub(r"\b(https?)\s*:?\s+//", r"\1://", text, flags=re.I)
    return SPACED_LINK.sub(lambda m: m.group(0).replace(" ", ""), text) if screen else text


def trim(text):
    """Drop sentence punctuation and unbalanced closing brackets from the end of a link or command."""
    text = text.rstrip(".,;:!?'\"”’ ")
    for close, open_ in ((")", "("), ("]", "["), ("}", "{")):
        while text.endswith(close) and text.count(close) > text.count(open_):
            text = text[:-1].rstrip(".,;:!?'\"”’ ")
    return text


class Found:
    """Ordered, de-duplicated items per kind, each with every place it was seen."""
    KINDS = ("links", "repos", "commands", "packages", "mcp", "tools", "handles", "gated")

    def __init__(self):
        self.items = {}

    def add(self, kind, value, where, key=None, **extra):
        value = value.strip()
        if not value:
            return
        item = self.items.setdefault(kind, {}).setdefault(key or value.lower(), {"value": value, "where": [], **extra})
        if where and where not in item["where"]:
            item["where"].append(where)

    def result(self):
        return {kind: list(self.items.get(kind, {}).values()) for kind in self.KINDS}


def repo_of(url):
    """github.com/owner/repo (also GitLab, Hugging Face, Codeberg) from a link, else None."""
    m = re.match(r"(?:https?://)?(?:www\.)?(" + "|".join(re.escape(h) for h in REPO_HOSTS) +
                 r")/([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)", url, re.I)
    if not m or m.group(2).lower() in NOT_OWNERS:
        return None
    return f"{m.group(1).lower()}/{m.group(2)}/{re.sub(r'[.]git$', '', m.group(3))}"


def covered(link, repos):
    """True when a repo line already says what this link says."""
    low = link.lower().rstrip("/")
    return any(r.lower() in low or low.endswith(r.split("/", 1)[1].lower()) for r in repos)


def words(cmd):
    try:
        return shlex.split(cmd, posix=True)
    except ValueError:  # an unbalanced quote, typical for OCR
        return cmd.split()


def positional(args, takes_value=()):
    """Arguments that aren't flags, skipping the values of flags known to take one."""
    out, skip = [], False
    for w in args:
        if skip:
            skip = False
        elif w.startswith("-"):
            skip = w in takes_value
        else:
            out.append(w)
    return out


def bare(pkg):
    """A package name without its version or extras: "react@18" -> "react", "@scope/x@1" -> "@scope/x",
    "yt-dlp[default]>=2026.8" -> "yt-dlp"."""
    if pkg.startswith("@"):
        scope, _, rest = pkg[1:].partition("/")
        return "@" + scope + "/" + re.split(r"[@\[]", rest)[0] if rest else pkg
    return re.split(r"[@=<>~!\[;]", pkg)[0]


def from_command(found, cmd, where):
    """Packages, repos and MCP servers named by one command."""
    w = words(cmd)
    if w and w[0] == "sudo":
        w = w[1:]
    if not w:
        return
    head, sub, rest = w[0].lower().lstrip("/"), (w[1].lower() if len(w) > 1 else ""), w[2:]

    def pkgs(kind, names):
        for p in names:
            name = bare(p)
            if PACKAGE.match(name) and not name.startswith("."):
                found.add("packages", f"{kind}: {name}", where)

    if head in ("npm", "pnpm", "yarn", "bun") and sub in ("i", "install", "add"):
        pkgs("npm", positional(rest))
    elif head in ("npx", "pnpx", "bunx") or (head in ("pnpm", "yarn", "bun") and sub in ("dlx", "x")) or \
            (head == "npm" and sub in ("exec", "x")):
        args = positional(w[1:] if head in ("npx", "pnpx", "bunx") else rest, ("-p", "--package"))
        if args:
            pkgs("npm", args[:1])
            if len(args) >= 3 and args[1] == "add" and REPO_ARG.match(args[2]):  # npx skills add owner/repo
                found.add("repos", f"github.com/{args[2]}", where)
    elif head in ("npm", "pnpm", "yarn", "bun") and sub == "create" and positional(rest):
        pkgs("npm", ["create-" + bare(positional(rest)[0])])
    elif head in ("pip", "pip3") and sub == "install":
        pkgs("pypi", positional(rest, ("-r", "-c", "-e", "-i", "--index-url", "--extra-index-url", "-t")))
    elif head == "uv" and sub in ("add", "pip", "tool"):
        args = positional(rest)
        pkgs("pypi", args[1:] if sub in ("pip", "tool") and args[:1] in (["install"], ["run"]) else args)
    elif head in ("pipx", "uvx"):
        pkgs("pypi", positional(w[1:] if head == "uvx" else rest, ("--from", "--with", "--python"))[:1])
    elif head == "brew" and sub == "install":
        pkgs("brew", positional(rest))
    elif head == "ollama" and sub in ("run", "pull") and positional(rest):
        found.add("packages", f"ollama: {positional(rest)[0]}", where)
    elif head == "cargo" and sub == "install":
        pkgs("crate", positional(rest))
    elif head == "gh" and sub == "repo" and len(rest) >= 2 and REPO_ARG.match(rest[1]):
        found.add("repos", f"github.com/{rest[1]}", where)
    elif head == "claude" and sub == "mcp" and rest[:1] == ["add"]:
        args = positional(rest[1:], ("-t", "--transport", "-s", "--scope", "-e", "--env", "-H", "--header"))
        if args:
            found.add("mcp", args[0], where)
    elif head in ("plugin", "claude") and "marketplace" in [x.lower() for x in w] and "add" in w:
        i = w.index("add") + 1
        if i < len(w) and REPO_ARG.match(w[i]):
            found.add("repos", f"github.com/{w[i]}", where)
    elif head == "plugin" and sub == "install" and rest:
        found.add("packages", f"claude plugin: {rest[0]}", where)
    for x in w:
        r = repo_of(x[4:] if x.lower().startswith("git+") else x)
        if not r and x.startswith("git@"):
            r = repo_of(x[4:].replace(":", "/", 1))
        if r:
            found.add("repos", r, where)


def commands_in(line):
    """The command in one line of text, or None. Text before it ("Then run ...") marks prose, where the command
    ends at the next sentence break or "to/and/then"."""
    m = COMMAND_RE.search(line)
    if not m:
        return None
    cmd = re.sub(r"\s+", " ", m.group("cmd")).strip()
    if re.search(r"[A-Za-z]{3,}", line[:m.start("cmd")]):
        end = PROSE_END.search(cmd)
        cmd = cmd[:end.start()] if end else cmd
    cmd = trim(cmd)
    return cmd if len(cmd) >= 5 else None


def find_all(texts):
    """texts: [(where, text)]. where is a short label; a label starting with "said" marks speech."""
    found = Found()
    for where, text in texts:
        if not text:
            continue
        speech = where.startswith("said")
        text = ocr_fixes(spoken(text) if speech else text, screen=where.startswith(SCREEN))
        for raw in URL_RE.findall(text):
            url = trim(raw)
            found.add("links", url, where)
            r = repo_of(url)
            if r:
                found.add("repos", r, where)
        for m in DOMAIN_RE.finditer(URL_RE.sub(" ", text)):
            if len(m.group(1)) < 4 or re.fullmatch(r"[\d.]+", m.group(1)):
                continue
            link = m.group(1).lower() + trim(m.group(2) or "")
            found.add("links", link, where)
            r = repo_of(link)
            if r:
                found.add("repos", r, where)
        if not speech:  # spoken commands are never exact enough to quote
            for line in text.splitlines() + re.findall(r"`([^`\n]{4,})`", text):
                cmd = commands_in(line)
                if cmd:
                    risk = next((why for rx, why in RISKS if rx.search(cmd)), None)
                    found.add("commands", cmd, where, key=cmd, **({"risk": risk} if risk else {}))
                    from_command(found, cmd, where)
            for m in HANDLE_RE.finditer(text):
                found.add("handles", "@" + m.group(1), where)
        for rx in MCP_RE:
            for m in rx.finditer(text):
                name = m.group(1) if rx.groups else m.group(0)
                if name.lower() not in NOT_MCP:
                    found.add("mcp", name, where)
        for rx, label in GATED:
            for m in rx.finditer(text):
                found.add("gated", label.format(*m.groups()), where)
        for tool, rx in TOOL_RES:
            if rx.search(text):
                found.add("tools", tool, where)
    out = found.result()
    names = {t["value"] for t in out["tools"]}
    # "Claude" next to "Claude Code", or "Copilot" next to "GitHub Copilot", says the same thing twice
    out["tools"] = [t for t in out["tools"] if not (t["value"] == "Claude" and "Claude Code" in names)
                    and not (t["value"] == "Copilot" and "GitHub Copilot" in names)]
    return out


def flat(found, limit=8):
    """The most actionable items first, as one list of strings: repos, commands, packages, MCP, links."""
    repos = [r["value"] for r in found.get("repos", [])]
    out = []
    for kind in ("repos", "commands", "packages", "mcp", "links"):
        for item in found.get(kind, []):
            v = item["value"]
            if v not in out and not (kind == "links" and covered(v, repos)):
                out.append(v)
    return out[:limit]
