"""Compile one picture with the diagram preamble so tectonic caches its packages.

Run at image build. Reads the preamble from services/diagrams.py without importing
it (importing pulls in config, which needs runtime secrets).
"""

import ast
import pathlib
import subprocess
import sys
import tempfile

source = pathlib.Path(__file__).resolve().parents[1] / "services" / "diagrams.py"
constants = {
    node.targets[0].id: ast.literal_eval(node.value)
    for node in ast.parse(source.read_text()).body
    if isinstance(node, ast.Assign)
    and isinstance(node.targets[0], ast.Name)
    and node.targets[0].id in ("_PREAMBLE", "_POSTAMBLE")
}
picture = r"\begin{tikzpicture}\draw (0,0) to[R] (2,0);\begin{axis}\addplot {x};\end{axis}\end{tikzpicture}"

with tempfile.TemporaryDirectory() as work:
    tex = pathlib.Path(work) / "d.tex"
    tex.write_text(constants["_PREAMBLE"] + picture + constants["_POSTAMBLE"])
    sys.exit(subprocess.run(["tectonic", "-X", "compile", "--outdir", work, str(tex)]).returncode)
