"""Page image (or text) -> JSON, on a Cursor model (cursor_gateway/vision_once.mjs).

Every answer is cached on disk by (prompt version, model, image hash), so a re-run
of the pipeline costs no Cursor usage for pages it has already read, and a bad
batch can be re-assembled without re-extracting.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import queue
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "cursor_gateway" / "vision_once.mjs"
MODEL = os.getenv("CURSOR_EXTRACT_MODEL", "composer-2.5")
TIMEOUT_S = int(os.getenv("CURSOR_EXTRACT_TIMEOUT", "300"))
PER_KEY = 2  # four parallel calls on one key drew "Network request failed"; two did not


def _keys() -> list[str]:
    """CURSOR_EXTRACT_API_KEY plus CURSOR_EXTRACT_API_KEYS (comma-separated), from the
    environment or cursor_gateway/.env. A process can be pinned to a subset by setting
    CURSOR_EXTRACT_API_KEYS in its environment."""
    env: dict[str, str] = {}
    dotenv = WORKER.parent / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    if os.getenv("CURSOR_EXTRACT_API_KEYS"):
        found = os.environ["CURSOR_EXTRACT_API_KEYS"].split(",")
    else:
        found = [os.getenv("CURSOR_EXTRACT_API_KEY") or env.get("CURSOR_EXTRACT_API_KEY", "")]
        found += env.get("CURSOR_EXTRACT_API_KEYS", "").split(",")
    return list(dict.fromkeys(k.strip() for k in found if k.strip()))


KEYS = _keys()
CONCURRENCY = int(os.getenv("CURSOR_EXTRACT_CONCURRENCY", str(PER_KEY * max(1, len(KEYS)))))
# One slot per allowed in-flight call; a call borrows a slot and runs on that slot's key.
_SLOTS: "queue.Queue[str]" = queue.Queue()
for _key in KEYS:
    for _ in range(PER_KEY):
        _SLOTS.put(_key)


class VisionError(RuntimeError):
    pass


def _parse_json(text: str) -> Any:
    """The model is asked for bare JSON; tolerate a code fence or a sentence around it."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise VisionError(f"no JSON in reply: {text[:200]!r}")
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start : end + 1])


def ask(prompt: str, image: Path | list[Path] | None, *, cache_dir: Path, version: str, retries: int = 4,
        name: str = "", model: str | None = None) -> Any:
    """One image, several (in order), or None for a text-only call (cached by prompt)."""
    model = model or MODEL
    images = image if isinstance(image, list) else ([image] if image else [])
    payload = b"".join(i.read_bytes() for i in images) if images else prompt.encode()
    digest = hashlib.sha256(version.encode() + model.encode() + payload).hexdigest()[:24]
    cached = cache_dir / f"{name or (images[0].stem if images else 'text')}.{digest}.json"
    if cached.exists():
        return json.loads(cached.read_text())

    last = ""
    for attempt in range(retries + 1):
        if attempt:
            time.sleep((5, 15, 30, 60, 60)[attempt - 1])  # transient "Network request failed" clears with time
        job = json.dumps({"model": model, "prompt": prompt, "images": [str(i) for i in images]})
        key = _SLOTS.get() if KEYS else ""
        try:
            proc = subprocess.run(
                ["node", "--disable-warning=ExperimentalWarning", str(WORKER)],
                input=job, capture_output=True, text=True, timeout=TIMEOUT_S, cwd=WORKER.parent,
                env={**os.environ, "CURSOR_EXTRACT_API_KEY": key} if key else None,
            )
        except subprocess.TimeoutExpired:
            last = f"timed out after {TIMEOUT_S}s"
            continue
        finally:
            if key:
                _SLOTS.put(key)
        line = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        try:
            reply = json.loads(line[0])
        except json.JSONDecodeError:
            last = f"worker output unreadable: {(proc.stderr or proc.stdout)[:200]!r}"
            continue
        if reply.get("error"):
            last = reply["error"]
            if "rate_limit" in last or "invalid_api_key" in last:
                break  # retrying cannot help
            continue
        try:
            data = _parse_json(reply.get("text", ""))
        except (VisionError, json.JSONDecodeError) as exc:
            last = str(exc)
            continue
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        return data
    raise VisionError(f"{name or (images[0].name if images else 'text call')}: {last}")


def read_pages(pdf: Path, pages: list[int], work: Path, *, prompt: str, version: str) -> list[dict[str, Any]]:
    """Read each page image with `prompt`, in parallel; results in page order, tagged `_page`."""
    from local_ingest.extract import pdf as pdfs

    def one(page: int) -> dict[str, Any]:
        image = pdfs.render(pdf, page, work / "pages")
        data = ask(prompt, image, cache_dir=work / "vision", version=version)
        data = data if isinstance(data, dict) else {"blocks": data}
        data["_page"] = page
        return data

    with ThreadPoolExecutor(CONCURRENCY) as pool:
        return list(pool.map(one, pages))


def pending(pdf: Path, pages: list[int], work: Path, *, prompt: str, version: str) -> int:
    """How many of these pages are not cached yet (each one costs a Cursor call)."""
    from local_ingest.extract import pdf as pdfs

    count = 0
    for page in pages:
        image = pdfs.render(pdf, page, work / "pages")
        digest = hashlib.sha256(version.encode() + MODEL.encode() + image.read_bytes()).hexdigest()[:24]
        count += not (work / "vision" / f"{image.stem}.{digest}.json").exists()
    return count
