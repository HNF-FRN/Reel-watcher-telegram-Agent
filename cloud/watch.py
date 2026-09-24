"""Run the existing reel watcher in a Claude cloud routine."""
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from client import BASE, auth_headers, call


def download(run, file, index):
    """Fetch one Telegram attachment through the Worker (the bot token stays in the Worker)."""
    ext = ".jpg" if file.get("kind") == "photo" else ".mp4"
    target = Path("reels") / f"telegram-{run['job_id']}-{index}{ext}"
    target.parent.mkdir(parents=True, exist_ok=True)
    query = urllib.parse.urlencode({"file_id": file["file_id"]})
    request = urllib.request.Request(BASE + "/backend/file?" + query, headers=auth_headers())
    with urllib.request.urlopen(request, timeout=120) as response, open(target, "wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    return str(target)


def main(run_id):
    run = call("GET", f"/backend/run/{run_id}")
    if run["action"] != "watch" or not run.get("job_id"):
        raise RuntimeError("watch.py only accepts a watch run")
    source = json.loads(call("GET", f"/backend/job/{run['job_id']}")["source"])
    if "url" in source:
        targets = [source["url"]]
    elif source.get("files") or source.get("file_id"):
        # An album is several photos in one job; reel.py takes them all at once.
        files = source.get("files") or [{"file_id": source["file_id"], "kind": source.get("kind")}]
        targets = [download(run, f, i) for i, f in enumerate(files, 1)]
    else:
        raise RuntimeError("No URL or Telegram attachment")
    script = Path(".claude/skills/reel-watch/scripts/reel.py")
    result = subprocess.run([sys.executable, str(script), *targets], text=True,
                            capture_output=True, encoding="utf-8", errors="replace")
    print(result.stdout)
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python cloud/watch.py RUN_ID")
    main(sys.argv[1])
