from agents.runtime.provenance import check_exam_claims, strip_foreign_images


def test_made_up_image_is_removed_drawn_diagram_kept(tmp_path, monkeypatch):
    from services import diagrams

    monkeypatch.setattr(diagrams, "SVG_DIR", tmp_path)
    drawn = "0123456789abcdef0123"
    (tmp_path / f"{drawn}.svg").write_text("<svg/>")
    text = (
        "Look:\n\n![track](https://example.com/jee_2016.png)\n\n"
        f"![circle](diagram:{drawn})\n\n![disk](diagram:jee_2016_p2_q)\n\n"
        "![never drawn](diagram:ffffffffffffffffffff)\n\nDone."
    )
    out, removed = strip_foreign_images(text)
    assert removed == 3
    assert f"diagram:{drawn}" in out
    assert "example.com" not in out and "jee_2016_p2_q" not in out and "ffffffff" not in out


def test_unbacked_pyq_claim_is_softened_and_flagged():
    text = "A famous question from JEE Advanced 2016 (Paper 2) involves a block on a track."
    out, unverified = check_exam_claims(text, chunks=[])
    assert unverified == ["JEE Advanced 2016 Paper 2"]
    assert '"JEE Advanced 2016 Paper 2"' in out
    assert "JEE Advanced 2016" not in out.split("\n\n", 1)[1]
    assert out.startswith("> **Note:**")


def test_claim_backed_by_retrieved_official_question_stays():
    chunks = [{"citation": "JEE Advanced 2016 Paper 2 — Q7 (official paper and final answer key)"}]
    text = "Here is JEE Advanced 2016 Paper 2 Q7:"
    out, unverified = check_exam_claims(text, chunks)
    assert out == text and not unverified


def test_year_only_claim_backed_by_any_paper_that_year():
    chunks = [{"citation": "JEE Advanced 2016 Paper 1 — Q6 (official paper and final answer key)"}]
    out, unverified = check_exam_claims("From JEE Advanced 2016:", chunks)
    assert not unverified


def test_wrong_paper_is_not_backed():
    chunks = [{"citation": "JEE Advanced 2016 Paper 1 — Q6"}]
    _, unverified = check_exam_claims("JEE Advanced 2016 Paper 2 asked this.", chunks)
    assert unverified


def test_jee_main_is_never_verified():
    # There are no official JEE Main papers in the corpus.
    _, unverified = check_exam_claims("This is from JEE Main 2019.", [{"citation": "JEE Main 2019"}])
    assert unverified


def test_claim_already_in_conversation_is_backed():
    history = ["Here is JEE Advanced 2023 Paper 1 Q4: ..."]
    _, unverified = check_exam_claims("In that JEE Advanced 2023 question, ...", [], history)
    assert not unverified


_PICTURE = "\\begin{tikzpicture}\n  \\draw[thick] (0,0) -- (2,0);\n\\end{tikzpicture}"


def _fake_render(monkeypatch, fail=False):
    from services import diagrams

    def render(src):
        if fail:
            raise diagrams.DiagramError("LaTeX error: ! Undefined control sequence.")
        assert src.startswith("\\begin{tikzpicture}") and src.endswith("\\end{tikzpicture}")
        return {"id": "0123456789abcdef0123", "path": "", "cached": True}

    monkeypatch.setattr(diagrams, "render_tikz", render)


def test_inline_tikz_becomes_a_drawn_diagram(monkeypatch):
    from agents.runtime.provenance import render_inline_tikz

    _fake_render(monkeypatch)
    for wrapped in (_PICTURE, f"```latex\n{_PICTURE}\n```", f"$$\n{_PICTURE}\n$$", f"\\[{_PICTURE}\\]"):
        out, drawn, dropped = render_inline_tikz(f"In the diagram below:\n\n{wrapped}\n\nThe force grows.")
        assert (drawn, dropped) == (1, 0)
        assert out == "In the diagram below:\n\n![Diagram](diagram:0123456789abcdef0123)\n\nThe force grows."


def test_inline_tikz_that_fails_is_removed_and_maths_around_it_kept(monkeypatch):
    from agents.runtime.provenance import render_inline_tikz

    _fake_render(monkeypatch, fail=True)
    out, drawn, dropped = render_inline_tikz(f"$$F = -kx$$\n{_PICTURE}\n$$T = 2\\pi\\sqrt{{m/k}}$$")
    assert (drawn, dropped) == (0, 1)
    assert "tikzpicture" not in out
    assert out == "$$F = -kx$$\n\n$$T = 2\\pi\\sqrt{m/k}$$"


def test_text_without_tikz_is_untouched():
    from agents.runtime.provenance import render_inline_tikz

    text = "$$\\begin{aligned} a &= b \\end{aligned}$$"
    assert render_inline_tikz(text) == (text, 0, 0)
