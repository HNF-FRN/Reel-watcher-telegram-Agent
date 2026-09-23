"""Run the existing reel watcher in a Claude cloud routine."""
import json
import subprocess
import sys
from pathlib import Path

from client import call


def main(run_id):
    run = call("GET", f"/backend/run/{run_id}")
    if run["action"] != "watch" or not run.get("job_id"):
        raise RuntimeError("watch.py only accepts a watch run")
    source = json.loads(call("GET", f"/backend/job/{run['job_id']}")["source"])
    if "url" in source:
        target = source["url"]
    elif "file_id" in source:
        from client import BASE, auth_headers
        import urllib.parse
        import urllib.request
        target = str(Path("reels") / f"telegram-{run['job_id']}.bin")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        query = urllib.parse.urlencode({"file_id": source["file_id"]})
        request = urllib.request.Request(BASE + "/backend/file?" + query,
                                         headers=auth_headers())
        with urllib.request.urlopen(request, timeout=120) as response, open(target, "wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        ext = ".jpg" if source.get("kind") == "photo" else ".mp4"
        named = target[:-4] + ext
        Path(target).rename(named)
        target = named
    else:
        raise RuntimeError("No URL or Telegram attachment")
    script = Path(".claude/skills/reel-watch/scripts/reel.py")
    result = subprocess.run([sys.executable, str(script), target], text=True,
                            capture_output=True, encoding="utf-8", errors="replace")
    print(result.stdout)
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python cloud/watch.py RUN_ID")
    main(sys.argv[1])

