"""Malformed markdown images must not reach the student as raw `![alt](url ...)` text."""

from agents.solver import _drop_malformed_images, prepare_student_markdown


def test_url_running_into_prose_becomes_its_alt_text():
    src = "| Straight | ![Slanted Line](https://latex.codecogs.com/svg.latex?x%20 increases steadily) | moves |"
    out = _drop_malformed_images(src)
    assert "![" not in out and "codecogs" not in out
    assert "Slanted Line" in out


def test_valid_images_are_untouched():
    for ok in (
        "![graph](https://example.com/a.svg)",
        '![graph](https://example.com/a.svg "Position-time graph")',
        "![graph](<https://example.com/with space.svg>)",
        "![](https://example.com/a.png)",
    ):
        assert _drop_malformed_images(ok) == ok


def test_two_broken_images_in_one_text_are_both_fixed():
    out = _drop_malformed_images("![A](http://h/x y) and ![B](http://h/ok.svg) and ![C](http://h/p q r)")
    assert out == "A and ![B](http://h/ok.svg) and C"


def test_outbound_text_is_cleaned_and_maths_still_renders():
    out = prepare_student_markdown("Slope $m = \\frac{\\Delta x}{\\Delta t}$ ![Line](https://h/svg?x%20 goes up)")
    assert "![Line]" not in out
    assert "\\frac" in out
