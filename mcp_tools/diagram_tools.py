"""draw_diagram — the tutor's way to show a picture instead of describing one.

The model writes TikZ / circuitikz (the same code it would write in Overleaf),
services/diagrams.py compiles it to SVG, and the tool hands back a one-line
markdown image to paste into the reply. A compile error comes back as a tool
error with the LaTeX message, so the model can fix the drawing and call again.
"""

from __future__ import annotations

from typing import Any, Dict

from pydantic import AliasChoices, BaseModel, Field

from agents.runtime.toolbelt import SCOPE_READ, ToolContext, tool


class DrawDiagramArgs(BaseModel):
    # Models often send `tikz_code` and skip the caption; a near-miss call should
    tikz: str = Field(
        ...,
        validation_alias=AliasChoices("tikz", "tikz_code", "code"),
        description=(
            "Complete \\begin{tikzpicture}…\\end{tikzpicture} or \\begin{circuitikz}…\\end{circuitikz} "
            "environment. Black only, ~6-8 cm wide. Math labels in $...$."
        ),
    )
    caption: str = Field(
        default="Diagram",
        description="Short caption for diagram.",
    )


@tool(
    name="draw_diagram",
    description=(
        "Render a diagram (physics diagrams like circuits and free-body diagrams, chemistry diagrams "
        "like reaction energy profiles and mechanism pathways, mathematics graphs) "
        "from TikZ code and return markdown image to put in your reply."
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
