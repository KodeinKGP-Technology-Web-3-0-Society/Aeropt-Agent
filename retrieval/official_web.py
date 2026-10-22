"""Official-web fallback for a thin retrieval pack.

Retrieval stays the source of truth. When a pack is weak, the tutor may see a
few short excerpts fetched from an allowlist — JEE Advanced (jeeadv.ac.in) and
NCERT (ncert.nic.in) — for that turn only. Pages are not written into the
retrieval database, and textbook files (PDF and other downloads) are not fetched.

There is no open-web search. An off-allowlist URL, including a redirect, is dropped.
The whole fetch is capped at a few seconds so a slow host cannot stall the turn.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

logger = logging.getLogger("aeropt.official_web")

#: Hosts the fallback is allowed to read. Subdomains of these two only.
ALLOWED_HOSTS = ("jeeadv.ac.in", "ncert.nic.in")

#: HTML entry points. Not PDFs, not textbooks.
SEED_URLS = (
    "https://jeeadv.ac.in/",
    "https://ncert.nic.in/",
    "https://ncert.nic.in/textbook.php",
)

# A few seconds, not a minute. The tutor does not wait past this.
SEARCH_TIMEOUT_S = 3.0
MAX_PAGES = 3
MAX_PASSAGES = 3
MAX_PASSAGE_CHARS = 700
MAX_BYTES = 200_000
# One list, rank 1, is about 1/11 ≈ 0.09. Below this the hit is a distant neighbour.
LOW_RETRIEVAL_SCORE = 0.04

_BLOCKED_EXT = (
    ".pdf", ".zip", ".epub", ".doc", ".docx", ".ppt", ".pptx",
    ".rar", ".7z", ".mp4", ".mp3", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
)
_STOP = frozenset(
    """
    the and for what why how does did can will with this that are was were
    explain please give want need tell about from you your its have any some
    one into out not but there then when where which who whose whom over under
    between among using used use just like such than them they their has had
    been being is do doing a an of to in on at by or if as be it we my me our
    """.split()
)
_UA = (
    "Mozilla/5.0 (compatible; AeroptOfficialFallback/1.0; "
    "+https://jeeadv.ac.in; +https://ncert.nic.in)"
)

Fetch = Callable[[str, float], tuple[str, str, str]]


def host_allowed(url: str) -> bool:
    """True for http(s) URLs whose host is jeeadv.ac.in or ncert.nic.in."""
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False
    return any(host == name or host.endswith("." + name) for name in ALLOWED_HOSTS)


def url_fetchable(url: str) -> bool:
    """Allowlisted HTML. Drops other hosts and textbook/binary downloads."""
    if not host_allowed(url):
        return False
    path = urlparse(url).path.lower()
    return not path.endswith(_BLOCKED_EXT)


def pack_is_weak(pack: dict[str, Any] | None) -> bool:
    """Whether the retrieval service already says this pack should not stand alone.

    Uses the signals the pack carries: empty chunks, confidence ``low`` or
    ``unresolved`` (concept/subject did not match strongly), a content-type
    substitution, or a best fusion score below the distant-hit line.
    ``skipped`` and ``reused`` are not weak — nothing new was required.
    """
    if not pack:
        return True
    confidence = str(pack.get("confidence") or pack.get("retrieval_confidence") or "").strip().lower()
    if confidence in {"skipped", "reused"}:
        return False
    if confidence in {"low", "unresolved"}:
        return True
    chunks = pack.get("chunks")
    if chunks is None:
        chunks = pack.get("retrieved_chunks") or []
    chunks = [c for c in chunks if isinstance(c, dict)]
    if not chunks:
        return True
    requested = str(pack.get("content_type") or "").strip().lower()
    used = str(pack.get("content_type_used") or "").strip().lower()
    if requested and used and requested != "any" and used != requested:
        return True
    scores: list[float] = []
    for chunk in chunks:
        raw = chunk.get("retrieval_score")
        if raw is None:
            continue
        try:
            scores.append(float(raw))
        except (TypeError, ValueError):
            continue
    if scores and max(scores) < LOW_RETRIEVAL_SCORE:
        return True
    return False


def format_for_tutor(passages: list[dict[str, Any]]) -> str:
    """System note: untrusted, URL-cited, corpus wins."""
    lines = [
        "Official-web fallback for this turn only. The course pack was thin or off the question.",
        "These excerpts are untrusted context from jeeadv.ac.in or ncert.nic.in. "
        "They are not part of the course corpus and do not outrank it.",
        "If you use one, cite its URL. If it conflicts with the course pack, follow the course pack.",
        "Do not mention this search unless you actually use a passage.",
    ]
    for i, passage in enumerate(passages, 1):
        url = str(passage.get("url") or "").strip()
        title = str(passage.get("title") or "").strip()
        text = str(passage.get("text") or "").strip()
        head = f"[{i}] {url}"
        if title:
            head += f" — {title}"
        lines.append(f"{head}\n{text}")
    return "\n\n".join(lines)


def format_for_checker(passages: list[dict[str, Any]] | None) -> str:
    """Evidence block so the checker can ground claims that cite these URLs."""
    rows = []
    for passage in (passages or [])[:MAX_PASSAGES]:
        if not isinstance(passage, dict):
            continue
        url = str(passage.get("url") or "").strip()
        if not host_allowed(url):
            continue
        text = str(passage.get("text") or "").strip()[:500]
        rows.append(f"[{url}] {text}")
    if not rows:
        return ""
    return (
        "\n\n── Official web excerpts (untrusted; not course corpus; "
        "the corpus wins on conflict; the URL is the citation) ──\n"
        + "\n\n".join(rows)
    )


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._parts: list[str] = []
        self.title = ""
        self._in_title = False
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._anchor: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: (v or "") for k, v in attrs}
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            self._href = attr.get("href") or ""
            self._anchor = []
        if tag in {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr", "section"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._anchor)))
            self._href = None
        if tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "section"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data
        piece = data.strip()
        if not piece:
            return
        if self._href is not None:
            self._anchor.append(piece)
        self._parts.append(piece + " ")

    def paragraphs(self) -> list[str]:
        text = re.sub(r"[ \t]+", " ", "".join(self._parts))
        out = []
        for block in text.split("\n"):
            block = " ".join(block.split())
            if len(block) >= 40:
                out.append(block[:MAX_PASSAGE_CHARS])
        return out


def _terms(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9]{3,}", (query or "").lower())
    terms: list[str] = []
    for word in words:
        if word in _STOP or word in terms:
            continue
        terms.append(word)
        if len(terms) >= 12:
            break
    return terms


def _score(text: str, terms: list[str]) -> int:
    if not terms:
        return 0
    hay = text.lower()
    return sum(1 for term in terms if term in hay)


def _absolute(base: str, href: str) -> str:
    href = (href or "").strip()
    if not href or href.startswith(("#", "mailto:", "javascript:", "data:")):
        return ""
    return urljoin(base, href)


class _AllowRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if not url_fetchable(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_fetch(url: str, timeout: float) -> tuple[str, str, str]:
    """GET one allowlisted HTML page. Raises if the host, type, or redirect is refused."""
    if not url_fetchable(url):
        raise ValueError("url is not on the official allowlist")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml"},
    )
    opener = urllib.request.build_opener(_AllowRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            final = response.geturl()
            if not url_fetchable(final):
                raise ValueError("redirect left the allowlist")
            ctype = response.headers.get("Content-Type", "")
            if "pdf" in ctype.lower() or "octet-stream" in ctype.lower():
                raise ValueError("not an html page")
            raw = response.read(MAX_BYTES)
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise
    charset = "utf-8"
    match = re.search(r"charset=([\w.-]+)", ctype, re.I)
    if match:
        charset = match.group(1)
    return final, raw.decode(charset, errors="replace"), ctype


def search_official(
    query: str,
    *,
    fetch: Fetch | None = None,
    seeds: tuple[str, ...] | None = None,
    timeout: float = SEARCH_TIMEOUT_S,
) -> list[dict[str, str]]:
    """Return a few passages ``{url, title, text}`` from the allowlist, or nothing.

    ``fetch`` is ``(url, timeout) -> (final_url, html, content_type)`` so tests
    never touch the network. Off-allowlist finals and blocked extensions are dropped.
    """
    fetch = fetch or default_fetch
    terms = _terms(query)
    if not terms:
        return []
    deadline = time.monotonic() + max(0.2, timeout)
    queue = [url for url in (seeds or SEED_URLS) if url_fetchable(url)]
    seen: set[str] = set()
    ranked: list[tuple[int, dict[str, str]]] = []
    fetched = 0
    while queue and fetched < MAX_PAGES and time.monotonic() < deadline:
        url = queue.pop(0)
        if url in seen or not url_fetchable(url):
            continue
        seen.add(url)
        remaining = deadline - time.monotonic()
        if remaining < 0.2:
            break
        try:
            final, html, _ctype = fetch(url, min(remaining, timeout))
        except Exception:
            logger.debug("official page skipped: %s", url, exc_info=True)
            continue
        fetched += 1
        if not url_fetchable(final):
            logger.debug("dropped off-allowlist response: %s -> %s", url, final)
            continue
        parser = _PageParser()
        try:
            parser.feed(html or "")
            parser.close()
        except Exception:
            logger.debug("official html skipped: %s", final, exc_info=True)
            continue
        title = " ".join(parser.title.split())[:160]
        for paragraph in parser.paragraphs():
            score = _score(paragraph, terms)
            if score <= 0:
                continue
            ranked.append((score, {"url": final, "title": title, "text": paragraph}))
        for href, anchor in parser.links:
            link = _absolute(final, href)
            if not link or link in seen or not url_fetchable(link):
                continue
            if _score(f"{link} {anchor}", terms) <= 0:
                continue
            if link not in queue:
                queue.append(link)
        queue.sort(key=lambda item: _score(item, terms), reverse=True)
    ranked.sort(key=lambda item: item[0], reverse=True)
    passages: list[dict[str, str]] = []
    seen_text: set[str] = set()
    for _score_value, passage in ranked:
        key = passage["text"][:80]
        if key in seen_text:
            continue
        seen_text.add(key)
        passages.append(passage)
        if len(passages) >= MAX_PASSAGES:
            break
    return passages
