"""Official-web fallback: weak packs only, allowlisted hosts, no wait on a late fetch."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agents.answer_checker import _format_evidence
from agents.runtime.loop import run_agent_loop
from agents.runtime.toolbelt import ToolContext, Toolbelt
from agents.runtime.web_fallback import WebFallback
from mcp_tools.curriculum_tools import RetrieveContentArgs, retrieve_content
from retrieval.official_web import (
    SEARCH_TIMEOUT_S,
    format_for_checker,
    format_for_tutor,
    pack_is_weak,
    search_official,
    url_fetchable,
)

_PASSAGE = {
    "url": "https://jeeadv.ac.in/syllabus",
    "title": "Syllabus",
    "text": "The torque on a magnetic dipole is the moment crossed with the field.",
}


def _strong_pack() -> dict:
    return {
        "confidence": "ok",
        "retrieved": True,
        "content_type": "theory",
        "content_type_used": "theory",
        "chunks": [
            {"retrieval_score": 0.2, "content_type": "Theory"},
            {"retrieval_score": 0.15, "content_type": "Theory"},
        ],
    }


def test_weak_pack_triggers_search():
    assert pack_is_weak({"confidence": "low", "chunks": [{"retrieval_score": 0.4}]})
    assert pack_is_weak({"confidence": "unresolved", "retrieved": False, "chunks": []})
    assert pack_is_weak({"confidence": "ok", "content_type": "theory", "content_type_used": "theory", "chunks": []})
    assert pack_is_weak(
        {
            "confidence": "ok",
            "content_type": "pyq",
            "content_type_used": "practice",
            "chunks": [{"retrieval_score": 0.4}, {"retrieval_score": 0.3}],
        }
    )
    assert pack_is_weak(
        {
            "confidence": "ok",
            "content_type": "theory",
            "content_type_used": "theory",
            "chunks": [{"retrieval_score": 0.01}, {"retrieval_score": 0.02}],
        }
    )

    calls: list[str] = []

    def search(query: str):
        calls.append(query)
        return [_PASSAGE]

    fallback = WebFallback(search=search)
    fallback.note_weak("torque on a magnetic dipole")
    assert fallback._search_future is not None
    assert fallback._search_future.result(timeout=2) == [_PASSAGE]
    assert calls == ["torque on a magnetic dipole"]


def test_strong_pack_does_not_start_search():
    assert not pack_is_weak(_strong_pack())
    assert not pack_is_weak({"confidence": "skipped", "chunks": []})
    assert not pack_is_weak({"confidence": "reused", "chunks": []})
    assert not pack_is_weak(
        {
            "confidence": "ok",
            "content_type": "pyq",
            "content_type_used": "pyq",
            "chunks": [{"retrieval_score": 1.0}],
        }
    )

    def search(_query: str):
        raise AssertionError("strong pack must not search the web")

    fallback = WebFallback(search=search)
    fallback.note_strong()
    fallback.note_weak("torque")
    assert fallback._search_future is None

    started = threading.Event()
    release = threading.Event()

    def slow(query: str):
        started.set()
        release.wait(2)
        return [_PASSAGE | {"text": query}]

    inflight = WebFallback(search=slow)
    inflight.note_weak("torque")
    assert started.wait(1)
    inflight.note_strong()
    release.set()
    inflight._search_future.result(timeout=2)
    assert inflight.take_for_model() is None


def test_disallowed_hosts_are_dropped():
    assert not url_fetchable("https://evil.example/book")
    assert not url_fetchable("https://jeeadv.ac.in.evil.com/a")
    assert not url_fetchable("https://ncert.nic.in/textbook/keph101.pdf")
    assert not url_fetchable("https://user:pass@jeeadv.ac.in/syllabus")
    assert url_fetchable("https://www.jeeadv.ac.in/syllabus")
    assert url_fetchable("https://ncert.nic.in/textbook.php")

    fetched: list[str] = []
    seed = """
    <html><title>JEE Advanced</title>
    <a href="https://evil.example/hcv.pdf">torque textbook</a>
    <a href="https://ncert.nic.in/keph.pdf">torque chapter</a>
    <a href="/syllabus.html">rotational torque syllabus</a>
    <p>The torque on a magnetic dipole is the moment crossed with the field.</p>
    </html>
    """

    def fetch(url: str, timeout: float):
        fetched.append(url)
        assert url_fetchable(url)
        assert 0 < timeout <= SEARCH_TIMEOUT_S
        if "syllabus.html" in url:
            page = "<p>Torque is listed under rotational motion in the official syllabus.</p>"
            return "https://jeeadv.ac.in/syllabus.html", page, "text/html"
        return "https://jeeadv.ac.in/", seed, "text/html"

    passages = search_official(
        "torque magnetic dipole",
        fetch=fetch,
        seeds=("https://evil.example/start", "https://jeeadv.ac.in/"),
    )
    assert passages
    assert all(url_fetchable(p["url"]) for p in passages)
    assert all("evil" not in p["url"] and not p["url"].endswith(".pdf") for p in passages)
    assert not any("evil" in url or url.endswith(".pdf") for url in fetched)

    def redirected(url: str, timeout: float):
        return "https://evil.example/stolen", "<p>torque textbook body</p>", "text/html"

    assert search_official("torque", fetch=redirected, seeds=("https://jeeadv.ac.in/",)) == []


def test_passages_include_the_url():
    html = (
        "<html><title>Syllabus</title>"
        "<p>The torque on a magnetic dipole is m cross B in the official syllabus.</p>"
        "</html>"
    )

    def fetch(url: str, timeout: float):
        assert url == "https://ncert.nic.in/"
        assert timeout <= SEARCH_TIMEOUT_S
        return "https://ncert.nic.in/textbook.php", html, "text/html"

    passages = search_official(
        "torque magnetic dipole",
        fetch=fetch,
        seeds=("https://ncert.nic.in/",),
    )
    assert len(passages) == 1
    assert passages[0]["url"] == "https://ncert.nic.in/textbook.php"
    assert "torque" in passages[0]["text"].lower()
    assert "https://ncert.nic.in/textbook.php" in format_for_tutor(passages)
    evidence = format_for_checker(passages)
    assert "https://ncert.nic.in/textbook.php" in evidence
    assert "untrusted" in evidence
    checker = _format_evidence([], passages)
    assert "https://ncert.nic.in/textbook.php" in checker
    assert "evil.example" not in _format_evidence(
        [],
        [{"url": "https://evil.example/book", "text": "pirated torque chapter"}],
    )


def test_late_search_is_ignored():
    release = threading.Event()

    def search(_query: str):
        release.wait(3)
        return [_PASSAGE]

    ctx = ToolContext(user_id="u1", session_id="s1", state={"query": "torque"})
    ctx.web_fallback = WebFallback(search=search)
    ctx.web_fallback.note_weak("torque")
    belt = Toolbelt([], ctx)

    def chat(**kwargs):
        blob = json.dumps(kwargs["messages"])
        assert "jeeadv.ac.in/syllabus" not in blob
        return SimpleNamespace(content="Torque is r cross F.", tool_calls=None), "m"

    try:
        with patch("agents.runtime.loop.chat_completion", side_effect=chat):
            result = run_agent_loop(
                system_prompt="tutor",
                user_message="what is torque?",
                toolbelt=belt,
                model="test",
                client=MagicMock(),
                label="test",
            )
    finally:
        release.set()
    assert result.text == "Torque is r cross F."
    assert "web_passages" not in ctx.state
    assert "web_passages" not in ctx.updates
    ctx.web_fallback._search_future.result(timeout=2)
    assert ctx.web_fallback.take_for_model() is None
    assert "web_passages" not in ctx.state


def test_ready_search_is_attached_before_the_reply():
    ctx = ToolContext(user_id="u1", session_id="s1", state={})
    ctx.web_fallback = WebFallback(search=lambda _q: [_PASSAGE])
    ctx.web_fallback.note_weak("torque")
    ctx.web_fallback._search_future.result(timeout=2)
    belt = Toolbelt([], ctx)
    seen: dict = {}

    def chat(**kwargs):
        seen["messages"] = kwargs["messages"]
        return SimpleNamespace(content="See the syllabus.", tool_calls=None), "m"

    with patch("agents.runtime.loop.chat_completion", side_effect=chat):
        run_agent_loop(
            system_prompt="tutor",
            user_message="torque?",
            toolbelt=belt,
            model="test",
            client=MagicMock(),
            label="test",
        )
    blob = json.dumps(seen["messages"])
    assert "https://jeeadv.ac.in/syllabus" in blob
    assert ctx.state["web_passages"][0]["url"] == "https://jeeadv.ac.in/syllabus"
    assert "web_passages" not in ctx.updates


def test_retrieve_content_searches_only_when_the_pack_is_weak(monkeypatch):
    calls: list[str] = []

    def search(query: str):
        calls.append(query)
        return [_PASSAGE]

    def run(result: dict, content_type: str = "theory"):
        ctx = ToolContext(
            user_id="u1",
            session_id="s1",
            state={"query": "torque on a magnetic dipole"},
        )
        ctx.web_fallback = WebFallback(search=search)
        monkeypatch.setattr(
            "retrieval.bridge.retrieve_for_state",
            lambda _state: result,
        )
        retrieve_content(
            RetrieveContentArgs(concept_id="torque", content_type=content_type),
            ctx,
        )
        if ctx.web_fallback._search_future is not None:
            ctx.web_fallback._search_future.result(timeout=2)
        return ctx

    weak = run({"retrieved_chunks": [], "retrieval_confidence": "low", "retrieval_notes": []})
    assert calls == ["torque on a magnetic dipole"]
    assert weak.web_fallback._void is False

    calls.clear()
    strong = run(
        {
            "retrieved_chunks": [
                {"content_id": "c1", "content_type": "Theory", "core_text": "Torque is r cross F. " * 4,
                 "citation": "NCERT — Rotational Motion", "retrieval_score": 0.2},
                {"content_id": "c2", "content_type": "Theory", "core_text": "A dipole feels a torque. " * 4,
                 "citation": "NCERT — Magnetism", "retrieval_score": 0.16},
            ],
            "retrieval_confidence": "ok",
            "content_type_requested": "theory",
            "retrieval_notes": [],
        }
    )
    assert calls == []
    assert strong.web_fallback._search_future is None
