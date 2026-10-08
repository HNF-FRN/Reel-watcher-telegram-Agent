"""verify.py - check what a reel names before you build on it (/deeper N in reelbot.py).

Every GitHub repo, npm package and PyPI package a breakdown found is looked up where it lives: does it exist, how
many stars or weekly downloads, its license, when it last changed, archived or deprecated. Public APIs only, no
key needed (GITHUB_TOKEN raises GitHub's rate limit). No model is involved: each line is what the registry says,
or "couldn't check".

    python verify.py <reel folder>        writes research.md there and prints the Telegram card
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GITHUB = "https://api.github.com"
NPM = "https://registry.npmjs.org"
NPM_DOWNLOADS = "https://api.npmjs.org/downloads/point/last-week"
PYPI = "https://pypi.org/pypi"
MAX_CHECKS = 8


def get(url, timeout=15):
    """(HTTP status, JSON) from a public API. A missing page is (404, None); no answer is (None, why)."""
    headers = {"User-Agent": "reel-agent", "Accept": "application/json"}
    if url.startswith(GITHUB) and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, str(e)[:120]


def short(n):
    """12345 -> "12.3k"."""
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n / 1e3:.1f}k".replace(".0k", "k")
    return str(n)


def day_and_age(stamp):
    """"2026-09-30T12:00:00Z" -> ("2026-09-30", days ago), or (None, None)."""
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None, None
    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return t.date().isoformat(), (datetime.now(timezone.utc) - t).days


def unreachable(status, why):
    return {"ok": None, "why": "couldn't check: " + ("rate limited, try later" if status in (403, 429) else
                                                    f"HTTP {status}" if status else why or "no answer")}


def github(repo):
    """repo: "github.com/owner/name"."""
    name = repo.split("/", 1)[1]
    status, d = get(f"{GITHUB}/repos/{urllib.parse.quote(name)}")
    if status == 404:
        return {"ok": False, "why": "not on GitHub (deleted, private or misspelled)"}
    if status != 200 or not isinstance(d, dict):
        return unreachable(status, d)
    day, age = day_and_age(d.get("pushed_at"))
    lic = (d.get("license") or {}).get("spdx_id")
    facts = [f"★ {short(d.get('stargazers_count') or 0)}",
             "custom license" if lic == "NOASSERTION" else lic or "no license", f"updated {day}" if day else None]
    if (d.get("full_name") or name).lower() != name.lower():
        facts.append(f"moved to {d['full_name']}")
    warn = "archived" if d.get("archived") else "no commits for over a year" if age and age > 365 else None
    return {"ok": True, "facts": [f for f in facts if f], "warn": warn, "about": (d.get("description") or "")[:200],
            "url": d.get("html_url") or f"https://{repo}"}


def npm(name):
    status, d = get(f"{NPM}/{urllib.parse.quote(name, safe='@')}/latest")
    if status == 404:
        return {"ok": False, "why": "not on npm"}
    if status != 200 or not isinstance(d, dict):
        return unreachable(status, d)
    s2, dl = get(f"{NPM_DOWNLOADS}/{urllib.parse.quote(name, safe='@/')}")
    weekly = dl.get("downloads") if s2 == 200 and isinstance(dl, dict) else None
    lic = d.get("license")
    lic = lic.get("type") if isinstance(lic, dict) else lic if isinstance(lic, str) else None
    facts = [f"v{d.get('version')}", f"{short(weekly)} downloads a week" if weekly is not None else None,
             lic or "no license"]
    warn = "deprecated" if d.get("deprecated") else "hardly anyone uses it" if weekly is not None and weekly < 50 \
        else None
    return {"ok": True, "facts": [f for f in facts if f], "warn": warn, "about": (d.get("description") or "")[:200],
            "url": f"https://www.npmjs.com/package/{name}"}


def pypi(name):
    status, d = get(f"{PYPI}/{urllib.parse.quote(name)}/json")
    if status == 404:
        return {"ok": False, "why": "not on PyPI"}
    if status != 200 or not isinstance(d, dict):
        return unreachable(status, d)
    info, files = d.get("info") or {}, d.get("urls") or []
    day, age = day_and_age((files[0].get("upload_time_iso_8601") or "") if files else "")
    lic = info.get("license_expression") or (info.get("license") if 0 < len(info.get("license") or "") <= 30 else None)
    facts = [f"v{info.get('version')}", lic or "license not stated", f"released {day}" if day else None]
    warn = "yanked" if files and all(f.get("yanked") for f in files) else \
        "no release for over two years" if age and age > 730 else None
    return {"ok": True, "facts": [f for f in facts if f], "warn": warn, "about": (info.get("summary") or "")[:200],
            "url": f"https://pypi.org/project/{name}/"}


CHECKS = {"github": github, "npm": npm, "pypi": pypi}


def targets(found):
    """(label, registry, name) for everything worth looking up: GitHub repos, npm and PyPI packages."""
    out = [(r["value"], "github", r["value"]) for r in found.get("repos") or []
           if r["value"].lower().startswith("github.com/")]
    for p in found.get("packages") or []:
        kind, _, name = p["value"].partition(": ")
        if kind in ("npm", "pypi") and name:
            out.append((p["value"], kind, name))
    return out[:MAX_CHECKS]


def check(found):
    """[(label, result)] where result["ok"] is True (exists), False (doesn't) or None (couldn't check)."""
    return [(label, CHECKS[kind](name)) for label, kind, name in targets(found)]


def verdict(results):
    if not results:
        return "nothing to check: it names no repo or package"
    missing = sum(1 for _, r in results if r["ok"] is False)
    unknown = sum(1 for _, r in results if r["ok"] is None)
    warned = sum(1 for _, r in results if r.get("warn"))
    if missing:
        return f"{missing} of {len(results)} not found: be careful"
    if unknown == len(results):
        return "couldn't reach the registries, try later"
    if warned:
        return f"it all exists, {warned} with a warning" if len(results) > 1 else "it exists, with a warning"
    return "everything it names exists" if len(results) > 1 else "it exists"


def line(label, r):
    label = label.replace("`", "'")
    if r["ok"] is False:
        return f"❌ `{label}`: {r['why']}"
    if r["ok"] is None:
        return f"❔ `{label}`: {r['why']}"
    return f"{'⚠️' if r.get('warn') else '✅'} `{label}`: " + " · ".join(r["facts"] + ([r["warn"]] if r.get("warn") else []))


def card(n, results):
    """The Telegram card for /deeper N."""
    rows = [f"🔎 **#{n} · {verdict(results)}**", ""]
    if results:
        rows += ["**Checked**"] + [f"- {line(label, r)}" for label, r in results] + \
                ["", "> Real isn't the same as safe: read the code before running it."]
    rows += ["", "**Next**", f"/plan {n}  plan it  ·  /save {n}  keep  ·  /dismiss {n}  skip"]
    return "\n".join(rows)


def research_md(n, results):
    """research.md: everything the registries said, with links."""
    rows = [f"# #{n}: what it names, checked", "",
            f"{verdict(results).capitalize()}. Checked {datetime.now():%Y-%m-%d %H:%M} against GitHub, npm and PyPI "
            "(public APIs, no model involved). Real isn't the same as safe: read the code before running it.", ""]
    for label, r in results:
        rows += [f"## {label}", "", f"- {line(label, r)}"]
        if r.get("about"):
            rows.append(f"- About: {r['about']}")
        if r.get("url"):
            rows.append(f"- Link: {r['url']}")
        rows.append("")
    return "\n".join(rows)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    folder = Path(sys.argv[1])
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    results = check(manifest.get("found") or {})
    (folder / "research.md").write_text(research_md("?", results), encoding="utf-8")
    print(card("?", results))


if __name__ == "__main__":
    main()
