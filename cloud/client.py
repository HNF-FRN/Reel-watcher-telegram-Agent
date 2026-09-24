"""Small authenticated bridge from a Claude cloud routine to the Telegram Worker."""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path


BASE = os.environ.get("REEL_CLOUD_URL", "").rstrip("/")
TOKEN = os.environ.get("REEL_CLOUD_BACKEND_TOKEN", "")


def auth_headers():
    # Cloudflare answers urllib's default "Python-urllib" User-Agent with
    # 403 (error 1010) before the Worker runs, so always send our own.
    # Claude cloud's API credential proxy adds Authorization for this host.
    # A local token remains useful for running the bridge outside cloud sessions.
    headers = {"User-Agent": "reel-agent-cloud/1"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    return headers


def call(method, path, body=None):
    if not BASE.startswith("https://"):
        raise RuntimeError("Set REEL_CLOUD_URL in the routine environment")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={**auth_headers(), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        content = response.read()
        return json.loads(content) if content else {}


def finish_card(item, args):
    """The last message of a run, as light Markdown: the Worker formats it and makes commands one tap."""
    n, action = item.get("job_id"), item.get("action")
    if args.status != "done":
        return f"❌ **{f'#{n} ' if n else ''}{action or 'task'} didn't finish**\n{args.summary}"
    if not n:
        return f"✅ {args.summary}"
    if action == "watch":
        return (f"🎬 **#{n} · {args.summary}**\n\n**Next**\n/r {n}  the breakdown  ·  /plan {n}  plan it\n"
                f"/save {n}  keep  ·  /dismiss {n}  skip")
    if action == "plan":
        return (f"📋 **#{n} plan ready**\n{args.summary}\n\n**Next**\n/r {n} full  read the plan\n"
                f"/build {n}  build it in the cloud")
    if action == "build":
        link = f"\n🔗 {args.branch_url}" if args.branch_url else ""
        return (f"✅ **#{n} build done**\n{args.summary}{link}\n\n"
                f"*Review the branch; it installs on your PC once the PC bot is back.*")
    return f"✅ **#{n}** {args.summary}"


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("run_id")
    get = sub.add_parser("get")
    get.add_argument("run_id")
    send = sub.add_parser("send")
    send.add_argument("run_id")
    send.add_argument("message")
    finish = sub.add_parser("finish")
    finish.add_argument("run_id")
    finish.add_argument("--status", choices=["done", "failed"], default="done")
    finish.add_argument("--summary", required=True)
    finish.add_argument("--breakdown")
    finish.add_argument("--plan")
    finish.add_argument("--branch-url")
    fetch = sub.add_parser("fetch-file")
    fetch.add_argument("run_id")
    fetch.add_argument("destination")
    args = parser.parse_args()

    if args.command == "start":
        session_id = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID")
        if not session_id:
            raise RuntimeError("This command is for a Claude cloud routine")
        call("POST", "/backend/session", {"run_id": args.run_id, "session_id": session_id})
        item = call("GET", f"/backend/run/{args.run_id}")
        item["job"] = call("GET", f"/backend/job/{item['job_id']}") if item.get("job_id") else None
        print(json.dumps(item, ensure_ascii=False))
    elif args.command == "get":
        item = call("GET", f"/backend/run/{args.run_id}")
        item["job"] = call("GET", f"/backend/job/{item['job_id']}") if item.get("job_id") else None
        print(json.dumps(item, ensure_ascii=False))
    elif args.command == "send":
        item = call("GET", f"/backend/run/{args.run_id}")
        call("POST", "/backend/message", {"chat_id": item["chat_id"], "text": args.message})
    elif args.command == "finish":
        item = call("GET", f"/backend/run/{args.run_id}")
        if item.get("job_id"):
            patch = {"status": "done" if args.status == "done" else "failed", "summary": args.summary}
            if args.breakdown:
                patch["breakdown"] = Path(args.breakdown).read_text(encoding="utf-8")
            if args.plan:
                patch["plan"] = Path(args.plan).read_text(encoding="utf-8")
            if args.branch_url:
                patch["branch_url"] = args.branch_url
            call("PATCH", f"/backend/job/{item['job_id']}", patch)
        call("PATCH", f"/backend/run/{args.run_id}", {"status": args.status})
        call("POST", "/backend/message", {"chat_id": item["chat_id"], "text": finish_card(item, args)})
    elif args.command == "fetch-file":
        item = call("GET", f"/backend/run/{args.run_id}")
        source = json.loads(call("GET", f"/backend/job/{item['job_id']}")["source"])
        file_id = source.get("file_id")
        if not file_id:
            raise RuntimeError("This job has no Telegram attachment")
        dest = Path(args.destination).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        query = urllib.parse.urlencode({"file_id": file_id})
        request = urllib.request.Request(BASE + "/backend/file?" + query,
                                         headers=auth_headers())
        with urllib.request.urlopen(request, timeout=120) as response, dest.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        print(str(dest))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"cloud bridge failed: {exc}", file=sys.stderr)
        sys.exit(1)

