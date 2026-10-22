# Agent tools

The complete current catalog is [docs/AGENT_TOOLS.md](docs/AGENT_TOOLS.md), with
all 24 registered tools, four Python helpers, schemas, scope/surface availability,
side effects and examples. This file explains selected math/physics tools.
The package name `mcp_tools` does not mean an MCP protocol server is implemented.

What the Main Tutor can actually do, and why each tool exists. Registered with `@tool`
in `mcp_tools/*.py`, imported from `agents/learning_agent.py::_import_tools` — a tool
module nothing imports is a tool the agent silently cannot use.

| tool | what it is for |
|---|---|
| `resolve_concept` | map what the student asked onto the syllabus |
| `retrieve_content` | fetch the course material to teach from (see `RETRIEVAL.md`) |
| `open_material` | open one specific item the student picked |
| `suggest_next_material` | what to offer next |
| `calculate` | exact arithmetic, no variables (`mcp_tools/compute_tools.py`) |
| **`solve_symbolically`** | rearrange, differentiate, integrate, approximate — exactly |
| **`check_dimensions`** | is this formula dimensionally possible? |
| **`verify_result`** | does my answer satisfy the equation, and behave in the limits? |
| **`check_student_work`** | which line of their working broke, with proof |
| **`convert_units`** | convert between units, refusing nonsense conversions |
| `draw_diagram` | compile TikZ/circuitikz to a cached SVG |
| `get_learner_context` | who am I teaching |
| `record_attempt` | record a question they attempted |
| `remember_about_learner` | save something durable |
| `schedule_review` | schedule a revisit |
| `consult_expert` | hand one narrow job to a temporary expert |

## `solve_symbolically`

`mcp_tools/symbolic_tools.py`, on sympy. JEE physics is algebra with numbers bolted on at
the end, and a rearrangement the tutor did mentally is the one part of an answer a
student cannot check for themselves.

| operation | example |
|---|---|
| `solve` | `m a = m g \sin\theta - \mu m g \cos\theta` for `a` → `g(\sin\theta - \mu\cos\theta)` |
| `differentiate` | `A\sin(\omega t)` → `A\omega\cos(\omega t)`; order 2 → `-A\omega^2\sin(\omega t)` |
| `integrate` | `k x` from `0` to `d` → `\frac{k d^2}{2}` |
| `approximate` | `\sin\theta` → `\theta - \frac{\theta^3}{6}`; relativistic KE → `\frac{mv^2}{2}` |
| `limit` | `\frac{\sin x}{x}` as `x\to 0` → `1` |
| `simplify` / `expand` / `factor` / `substitute` | with `substitutions` applied after any operation, giving the number too |

Three decisions worth knowing:

- **sympy never sees a raw string.** Input is parsed and whitelisted by
  `utils/mathexpr.py`, then the *validated AST* is converted to sympy expressions node
  by node. `sympify` on untrusted text executes code; this cannot.
- **Symbols are positive by default**, which is right for masses, lengths, times and
  coefficients of friction and lets `\sqrt{m^2}` simplify. The note in every `solve`
  result says so, because it means a genuinely negative root can be dropped.
- **Divergent limits are checked numerically.** At a point where an expression stops
  being real, sympy's sign depends on the branch it picks and is not always the physics
  (`v \to c` means from below). The samples are reported alongside, and a disagreement
  between them and the symbolic sign is flagged rather than resolved silently.

## The physics reasoning tools

`mcp_tools/physics_tools.py`. These exist because a tutor that is confidently wrong is
worse than one that admits uncertainty, and because the errors students actually make
are mechanically detectable.

### `check_dimensions`

Dimensional analysis over the formula, exact (`Fraction` exponents, so `sqrt` is `^1/2`
rather than `0.4999`). `v = \sqrt{2gh}` is a velocity; `v = \sqrt{2gh^2}` is `L^3/2 T^-1`
and therefore wrong, provably, in microseconds. It also catches:

- a force added to a momentum, or an acceleration subtracted from a force;
- `\sin` of something that is not an angle;
- an expression whose dimensions are simply not the quantity you wanted.

Physics reuses letters, so `T`, `I`, `P`, `E`, `A`, `L` and `sigma` are treated as
**ambiguous** and the tool will not guess — with one exception: when only one reading
makes an equation balance, that reading is deduced and reported. `T = 2\pi\sqrt{l/g}`
needs no annotation; `T` alone does.

A pass means "not wrong in this way". A wrong coefficient is dimensionally perfect.

### `verify_result`

Three checks on a result before a student sees it:

1. its dimensions, against what the unknown should be;
2. **substitution** — put the answer back into the governing equation and compare both
   sides at two dozen points. This is the strongest check available without a CAS;
3. **limiting cases** — `mu -> 0`, `m -> inf`. Give an expected limit and it is checked;
   give none and the obvious ones are probed for you to judge.

A sign slip that survives an arithmetic check does not survive this.

### `check_student_work`

Takes the student's steps and finds **the first one that does not follow**, with numbers
that prove it. Deciding equivalence without a CAS is done by evaluating at many random
points, and the criterion depends on the shape of the step:

| step shape | how it is decided |
|---|---|
| expression → expression | equal at every probe point |
| equation → equation, later one solved for the unknown | substitute it back; the earlier equation must leave zero |
| neither solved for anything | solve one numerically and require the other to vanish at that root |

That last rule matters. A constant-ratio test rejects `\frac{1}{2}mv^2 = mgh \to v^2 = 2gh`,
which is correct work; accepting any non-zero ratio passes
`ma = mg\sin\theta - \mu mg\cos\theta \to a = g\sin\theta - \mu mg\cos\theta`, which is
the single most common student error in this topic. Comparing roots gets both right.

Each step is also checked dimensionally, because a line can be wrong on its own.

Counterexamples are re-found at **physically plausible values** before being reported —
`mu` below 1, angles under 80°, `g = 9.81`. The wide random probes decide whether
something is wrong; those values would be useless in a sentence shown to a student.

### `convert_units`

Compound units parse as expressions (`kg*m/s**2`, `g/cm**3`), so nothing needs to be
enumerated. Converting between different quantities is refused with both dimensions
named, rather than silently returning a number.

## Safety

Everything goes through `utils/mathexpr.py`: parse to an AST, check every node against
a whitelist, evaluate only whitelisted functions. No `eval`, no imports, no attribute
access, no subscripting, no lambdas, no comprehensions. One whitelist shared by the
calculator and the physics tools, because two copies drift.

LaTeX is accepted as input (`\frac{v^2}{2g}`, `m g \sin\theta`, `\mu_k`) since the rest
of this service speaks LaTeX. Implicit multiplication is only inserted where it is
unambiguous — after a digit, after a bracket, or between two space-separated names.
`mgh` stays one symbol called `mgh`, because guessing it means `m*g*h` would silently
change a student's equation.

## Not here yet

- **Reading a photo of a student's work.** There is no image input anywhere in the
  stack, so `check_student_work` needs the steps transcribed. This is the biggest
  remaining gap for the "why is my answer wrong" case, since students photograph their
  page rather than typing it.
- **A photo/PDF material reader.** Diagrams are connected through `draw_diagram`,
  but interpreting uploaded student pages is still missing from the tutor graph.
- **Plotting a relation** (v–t, U(x), a phase plot). The cheapest of the three: the
  evaluator here already produces the numbers, so it needs only SVG output.
- **Numeric solving** for equations with no closed form. `solve_symbolically` returns
  nothing useful for those; a bracketing solver would cover them (the root-finder in
  `physics_tools._solve_for` already does this internally).
