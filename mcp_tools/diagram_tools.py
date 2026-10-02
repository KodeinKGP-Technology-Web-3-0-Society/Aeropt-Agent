"""draw_diagram — the tutor's way to show a picture instead of describing one.

The model writes TikZ / circuitikz (the same code it would write in Overleaf),
services/diagrams.py compiles it to SVG, and the tool hands back a one-line
markdown image to paste into the reply. A compile error comes back as a tool
error with the LaTeX message, so the model can fix the drawing and call again.
"""

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field

from agents.runtime.toolbelt import SCOPE_READ, ToolContext, tool


class DrawDiagramArgs(BaseModel):
    tikz: str = Field(
        ...,
        description=(
            "One complete \\begin{tikzpicture}…\\end{tikzpicture} or "
            "\\begin{circuitikz}…\\end{circuitikz} environment — nothing else. "
            "Preamble is provided: amsmath, circuitikz (siunitx), pgfplots, and TikZ "
            "libraries arrows.meta, calc, positioning, decorations.pathmorphing "
            "(springs: decorate, decoration={coil}), decorations.markings, patterns "
            "(hatched walls/ground: pattern=north east lines), angles, quotes. "
            "Labels go in $...$. Black only, about 6-8 cm wide."
        ),
    )
    caption: str = Field(
        ...,
        description="Short caption the student reads, e.g. 'LC circuit at t = 0'.",
    )


@tool(
    name="draw_diagram",
    description=(
        "Draw a physics diagram (circuit, free-body diagram, ray diagram, field "
        "lines, graph of a function, geometry of a setup) from TikZ code and get a "
        "markdown image to put in your reply. Use it whenever a picture would make "
        "the explanation or question clearer — and always when the student asks "
        "for a diagram. Paste the returned `markdown` into your reply exactly, on "
        "its own line. If it returns an error, fix the TikZ and call again (at "
        "most twice)."
    ),
    args_model=DrawDiagramArgs,
    scopes=(SCOPE_READ,),
)
def draw_diagram(args: DrawDiagramArgs, ctx: ToolContext) -> Dict[str, Any]:
    from services.diagrams import DiagramError, render_tikz

    caption = " ".join((args.caption or "Diagram").split())[:120].replace("]", ")").replace("[", "(")
    try:
        result = render_tikz(args.tikz)
    except DiagramError as exc:
        return {"error": str(exc), "hint": "Fix the TikZ and call draw_diagram again."}
    except Exception as exc:  # never let a renderer fault fail the turn
        return {"error": f"Diagram renderer failed: {exc}"}
    return {
        "ok": True,
        "diagram_id": result["id"],
        "markdown": f"![{caption}](diagram:{result['id']})",
    }
