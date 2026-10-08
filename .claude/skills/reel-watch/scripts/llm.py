"""llm.py - a small client for any OpenAI-compatible chat API: Ollama, llama.cpp's server, LM Studio, vLLM,
LocalAI, or a hosted endpoint. Standard library only.

    REEL_LLM_URL     base URL, default http://localhost:11434/v1 (Ollama)
    REEL_LLM_MODEL   model name; unset = ask the server and take a vision model if it has one, else the first
    REEL_LLM_KEY     API key, only if the server wants one
    REEL_LLM_VISION  on | off: send frames as images (default: guessed from the model name)
    REEL_LLM=off     never use a model (the open engine still writes a breakdown from OCR and the transcript)
"""
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://localhost:11434/v1"
VISION_HINTS = ("vl", "vision", "llava", "gemma3", "gemma-3", "minicpm-v", "moondream", "pixtral", "llama4",
                "mistral-small3", "granite3.2-vision", "bakllava", "internvl", "phi4-multimodal", "kimi-vl")
_resolved = {}


class LLMError(Exception):
    pass


def config():
    """The configured endpoint, or None when models are switched off."""
    if os.environ.get("REEL_LLM", "").strip().lower() in ("off", "0", "false", "no", "none"):
        return None
    return {"url": (os.environ.get("REEL_LLM_URL") or DEFAULT_URL).rstrip("/"),
            "model": os.environ.get("REEL_LLM_MODEL") or None, "key": os.environ.get("REEL_LLM_KEY") or None}


def request(method, url, key=None, body=None, timeout=900):
    headers = {"Content-Type": "application/json", "User-Agent": "reel-agent"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data, headers, method=method), timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise LLMError(f"HTTP {e.code} from {url}: {e.read().decode('utf-8', 'replace')[:300]}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise LLMError(f"no answer from {url}: {e}") from None


def list_models(cfg, timeout=3):
    """Model ids the server offers, or None if it doesn't answer."""
    try:
        data = request("GET", cfg["url"] + "/models", cfg["key"], timeout=timeout)
    except LLMError:
        return None
    return [m["id"] for m in data.get("data") or [] if isinstance(m, dict) and m.get("id")]


def is_vision(model):
    forced = os.environ.get("REEL_LLM_VISION", "").strip().lower()
    if forced in ("on", "1", "true", "yes"):
        return True
    if forced in ("off", "0", "false", "no"):
        return False
    return any(h in (model or "").lower() for h in VISION_HINTS)


def resolve(model=None, tools=False):
    """Settings with a model picked, or None when no server answers (or it has no chat model). Picking by itself,
    it prefers a vision model for watching, and for builds (tools=True) one without vision: small vision models
    often can't call tools. The answer is kept for a minute, so a long-running bot notices when Ollama starts."""
    cfg = config()
    if not cfg:
        return None
    want = model or cfg["model"]
    key = (cfg["url"], want, tools)
    if key not in _resolved or time.time() - _resolved[key][0] > 60:
        ids = list_models(cfg)
        chat = [i for i in ids or [] if "embed" not in i.lower()]
        if ids is None:
            found = None
        elif want:
            found = {**cfg, "model": want}  # trust the name: some servers list models differently
        else:
            vision = [i for i in chat if is_vision(i)]
            plain = [i for i in chat if not is_vision(i)]
            found = {**cfg, "model": ((plain if tools else vision) or chat)[0]} if chat else None
        _resolved[key] = (time.time(), found)
    return _resolved[key][1]


def chat(messages, cfg, tools=None, temperature=0.2, max_tokens=None, timeout=900, frequency_penalty=None):
    """One chat completion. Returns the assistant message: {"role", "content", "tool_calls"?}."""
    body = {"model": cfg["model"], "messages": messages, "temperature": temperature, "stream": False}
    if tools:
        body["tools"] = tools
    if max_tokens:
        body["max_tokens"] = max_tokens
    if frequency_penalty is not None:  # keeps small models from looping on one line
        body["frequency_penalty"] = frequency_penalty
    data = request("POST", cfg["url"] + "/chat/completions", cfg["key"], body, timeout)
    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"unexpected reply: {json.dumps(data)[:300]}") from None


def text(message):
    """The visible answer, without a reasoning model's <think> block."""
    content = message.get("content") or ""
    if isinstance(content, list):  # some servers return content parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()


def image_part(path):
    """An image for a vision model, inline as a data URL (OpenAI format; Ollama, llama.cpp and vLLM take it)."""
    ext = Path(path).suffix.lower()
    mime = {".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")
    data = base64.b64encode(Path(path).read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


def describe(cfg):
    """"qwen2.5vl:7b at localhost:11434" for messages and /quota."""
    host = re.sub(r"^https?://", "", cfg["url"]).split("/")[0]
    return f"{cfg['model']} at {host}"
