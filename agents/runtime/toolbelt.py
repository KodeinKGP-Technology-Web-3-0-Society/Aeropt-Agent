"""Typed toolbelt: the only way the agent touches the world.

Design rules, in priority order:

1. **Deterministic things stay deterministic.** Retrieval order, proficiency
   maths and grading are not improvised by a model — they are tools the agent
   *calls*. The agent decides *whether* and *with what*; the tool decides *how*.

2. **A tool never raises into the loop.** Bad arguments, a dead database, a
   missing concept — all come back as a structured failure the model can read
   and react to. A crash would end the student's turn; an error message lets the
   agent recover, which is the entire point of a reasoning loop.

3. **Capability is scoped, not assumed.** Each tool declares what it needs
   (`read`, `write:progress`, `write:memory`, `student_facing`). A caller is
   built with a scope allowlist, so an expert subagent physically cannot write
   memory or talk to the student — that is enforced here, not requested in a
   prompt.

4. **Consequential tools stop and ask.** Anything the learner would want a say
   in is marked `consequential` and returns a permission request instead of
   acting, unless the caller was granted that permission for the turn.
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Type

from pydantic import BaseModel, ValidationError

from agents.runtime.trace import AgentTrace, TraceStep

logger = logging.getLogger("jee_tutor.toolbelt")
_TOOL_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="tool_worker")
_CALL_TOKEN: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("tool_call_token", default=None)


def call_is_live(ctx: "ToolContext") -> bool:
    """False after this call's timeout or cancellation. A running thread cannot be killed."""
    token = _CALL_TOKEN.get()
    if token is None:
        return True
    return token in getattr(ctx, "_live_tokens", set())

# ── Scopes ────────────────────────────────────────────────────────────────────
SCOPE_READ = "read"                     # read learner/content data
SCOPE_WRITE_PROGRESS = "write:progress"  # mutate proficiency / attempts
SCOPE_WRITE_MEMORY = "write:memory"      # mutate durable learner memory
SCOPE_SCHEDULE = "write:schedule"        # schedule future review
SCOPE_COMPOSE = "compose"                # draft student-facing prose

#: What the Main Tutor may do. It owns the learner relationship.
TUTOR_SCOPES = frozenset(
    {SCOPE_READ, SCOPE_WRITE_PROGRESS, SCOPE_WRITE_MEMORY, SCOPE_SCHEDULE, SCOPE_COMPOSE}
)

#: What a temporary expert may do: look things up and report back. Nothing else.
#: An expert cannot write memory, cannot change progress, cannot schedule, and
#: cannot hand off again — enforced by this set plus `ToolContext.depth`.
EXPERT_SCOPES = frozenset({SCOPE_READ})


class ToolContext:
    """Per-turn execution context handed to every tool.

    Tools read the live turn state and *stage* their state changes in `updates`
    rather than mutating shared state directly, so the agent loop stays the one
    place that decides what actually lands on the turn.
    """

    def __init__(
        self,
        *,
        user_id: str,
        session_id: str,
        state: Optional[Dict[str, Any]] = None,
        trace: Optional[AgentTrace] = None,
        depth: int = 0,
        granted_permissions: Optional[Iterable[str]] = None,
        budget: Optional[Any] = None,
    ) -> None:
        self.user_id = user_id
        self.session_id = session_id
        self.state: Dict[str, Any] = state if state is not None else {}
        self.updates: Dict[str, Any] = {}
        self.trace = trace or AgentTrace(user_id=user_id, session_id=session_id)
        self.depth = depth
        self.granted_permissions = set(granted_permissions or ())
        self.budget = budget
        #: Permission requests raised by consequential tools this turn.
        self.pending_permissions: List[Dict[str, Any]] = []
        #: Results of read-only calls already made this turn, keyed by
        #: (tool, arguments). See Toolbelt.call for why.
        self._read_cache: Dict[str, "ToolResult"] = {}
        #: Unshaped tool outputs kept for downstream grading and grounding.
        self.full_tool_results: List[Dict[str, Any]] = []
        self._live_tokens: set = set()

    def stage(self, **updates: Any) -> None:
        """Record state changes for the loop to apply after the turn resolves."""
        token = _CALL_TOKEN.get()
        if token is not None and token not in self._live_tokens:
            return
        self.updates.update(updates)

    def open_call(self) -> str:
        token = uuid.uuid4().hex
        self._live_tokens.add(token)
        return token

    def seal_call(self, token: str) -> None:
        self._live_tokens.discard(token)

    def child(self, *, depth: Optional[int] = None) -> "ToolContext":
        """A restricted context for a subagent. Shares the trace, not the updates."""
        ctx = ToolContext(
            user_id=self.user_id,
            session_id=self.session_id,
            state=dict(self.state),
            trace=self.trace,
            depth=self.depth + 1 if depth is None else depth,
            budget=self.budget,
        )
        return ctx


def _shape_dict_result(payload: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
    """Shape a tool result payload to fit within max_chars without losing core grounding."""
    data = payload.get("data")
    if not isinstance(data, dict):
        return payload

    shaped_data = dict(data)
    chunks = shaped_data.get("chunks")
    if isinstance(chunks, list) and len(chunks) > 2:
        top_chunks = []
        for c in chunks[:2]:
            c_copy = dict(c)
            for text_key in ("text", "preview", "content", "core_text"):
                if text_key in c_copy and len(str(c_copy[text_key])) > 400:
                    c_copy[text_key] = str(c_copy[text_key])[:400] + "..."
            top_chunks.append(c_copy)
        remaining = len(chunks) - 2
        omitted_ids = [
            str(c.get("content_id") or c.get("id") or "")
            for c in chunks[2:]
            if (c.get("content_id") or c.get("id"))
        ]
        shaped_data["chunks"] = top_chunks
        shaped_data["more_available"] = (
            f"{remaining} more material chunks available. Call open_material(content_id) to inspect."
        )
        if omitted_ids:
            shaped_data["omitted_content_ids"] = omitted_ids[:10]
        payload = {**payload, "data": shaped_data}

    # If still over budget, truncate string fields gracefully within data dict
    try:
        cur_len = len(json.dumps(payload, default=str))
        if cur_len > max_chars:
            for k, v in list(shaped_data.items()):
                if isinstance(v, str) and len(v) > 200:
                    shaped_data[k] = v[:200] + "..."
                elif isinstance(v, list) and len(v) > 3:
                    shaped_data[k] = v[:3]
            payload = {**payload, "data": shaped_data}
    except Exception:
        pass

    return payload


@dataclass
class ToolResult:
    """What a tool hands back to the model."""

    ok: bool
    data: Any = None
    error: Optional[str] = None
    #: Set when the tool refused to act and wants the learner asked first.
    permission_required: Optional[Dict[str, Any]] = None

    def to_json(self, max_chars: Optional[int] = None) -> str:
        if self.permission_required:
            payload: Dict[str, Any] = {
                "ok": False,
                "permission_required": self.permission_required,
                "note": (
                    "This action was NOT performed. Ask the student for confirmation "
                    "in your reply, then call this tool again next turn if they agree."
                ),
            }
        elif self.ok:
            payload = {"ok": True, "data": self.data}
        else:
            payload = {"ok": False, "error": self.error}

        if max_chars is not None and self.ok:
            payload = _shape_dict_result(payload, max_chars)

        try:
            return json.dumps(payload, default=str, ensure_ascii=False)
        except Exception:
            return json.dumps({"ok": False, "error": "tool result was not serialisable"})


@dataclass(frozen=True)
class ToolSpec:
    """One callable capability, described well enough for a model to choose it."""

    name: str
    description: str
    args_model: Type[BaseModel]
    handler: Callable[[BaseModel, ToolContext], Any]
    scopes: frozenset = field(default_factory=lambda: frozenset({SCOPE_READ}))
    #: True when the learner should be asked before this runs.
    consequential: bool = False
    #: Maximum characters the model sees for this tool's result.
    max_result_chars: int = 2500
    #: Timeout in seconds for tool execution.
    timeout_s: float = 8.0

    def schema(self) -> Dict[str, Any]:
        """OpenAI/Groq function-calling schema for this tool."""
        params = self.args_model.model_json_schema()
        # Some providers reject $ref-bearing function schemas, so nested models are
        # inlined. Dropping $defs without substituting the references it holds — which
        # is what this did before — leaves a dangling $ref, and the tool is then either
        # rejected outright or quietly never called.
        params = _inline_refs(params, params.pop("$defs", {}) or {})
        params.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description.strip(),
                "parameters": params,
            },
        }


def _inline_refs(node: Any, defs: Dict[str, Any], depth: int = 0) -> Any:
    """Replace every {"$ref": "#/$defs/X"} with the definition of X, recursively.

    Bounded depth so a self-referential model cannot spin forever; at the limit the
    reference becomes a permissive object rather than a broken pointer.
    """
    if depth > 6:
        return {"type": "object"}
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = defs.get(ref.split("/")[-1])
            if target is None:
                return {"type": "object"}
            merged = {**_inline_refs(target, defs, depth + 1)}
            merged.pop("title", None)
            for key, value in node.items():
                if key != "$ref":
                    merged[key] = _inline_refs(value, defs, depth + 1)
            return merged
        return {key: _inline_refs(value, defs, depth + 1) for key, value in node.items()}
    if isinstance(node, list):
        return [_inline_refs(item, defs, depth + 1) for item in node]
    return node


# ── Decorator helper ──────────────────────────────────────────────────────────

_REGISTRY: Dict[str, ToolSpec] = {}


def tool(
    name: str,
    description: str,
    args_model: Type[BaseModel],
    *,
    scopes: Iterable[str] = (SCOPE_READ,),
    consequential: bool = False,
    max_result_chars: int = 2500,
    timeout_s: float = 8.0,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a function as an agent tool.

    The wrapped function keeps working as a plain Python call, so existing
    non-agent callers and unit tests are unaffected by registration.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        spec = ToolSpec(
            name=name,
            description=description,
            args_model=args_model,
            handler=fn,
            scopes=frozenset(scopes),
            consequential=consequential,
            max_result_chars=max_result_chars,
            timeout_s=timeout_s,
        )
        if name in _REGISTRY:
            logger.warning("Tool %s registered twice — overwriting.", name)
        _REGISTRY[name] = spec
        setattr(fn, "tool_spec", spec)
        return fn

    return decorator


def registry() -> Dict[str, ToolSpec]:
    """All tools registered so far. Import the tool modules before calling."""
    return dict(_REGISTRY)


def get_spec(name: str) -> Optional[ToolSpec]:
    return _REGISTRY.get(name)


# ── Per-Surface Tool Sets ────────────────────────────────────────────────────

SURFACE_TOOL_SETS: Dict[str, frozenset[str]] = {
    "chat": frozenset({
        "resolve_concept",
        "retrieve_content",
        "open_material",
        "suggest_next_material",
        "consult_expert",
        "calculate",
        "solve_symbolically",
        "verify_result",
        "check_dimensions",
        "convert_units",
        "check_student_work",
        "draw_diagram",
        "remember_about_learner",
        "get_learner_context",
    }),
    "study_learn": frozenset({
        "retrieve_content",
        "open_material",
        "suggest_next_material",
        "solve_symbolically",
        "calculate",
        "check_dimensions",
        "verify_result",
        "draw_diagram",
        "remember_about_learner",
        "schedule_review",
        "set_lesson_plan",
        "transition_phase",
        "complete_study_session",
        "complete_lesson_part",
    }),
    "study_practice": frozenset({
        "serve_practice_question",
        "grade_practice_answer",
        "solve_symbolically",
        "calculate",
        "verify_result",
        "check_dimensions",
        "check_student_work",
        "convert_units",
        "remember_about_learner",
        "schedule_review",
        "transition_phase",
        "complete_study_session",
    }),
    "classroom_lesson": frozenset({
        "retrieve_content",
        "open_material",
        "draw_diagram",
        "solve_symbolically",
        "calculate",
        "verify_result",
        "check_dimensions",
        "consult_expert",
        "remember_about_learner",
    }),
    "classroom_doubt": frozenset({
        "resolve_concept",
        "retrieve_content",
        "open_material",
        "consult_expert",
        "check_student_work",
        "solve_symbolically",
        "calculate",
        "verify_result",
        "check_dimensions",
        "convert_units",
        "remember_about_learner",
    }),
    "classroom_assess": frozenset({
        "retrieve_content",
        "check_student_work",
        "calculate",
        "solve_symbolically",
        "verify_result",
        "check_dimensions",
        "remember_about_learner",
    }),
    "coach": frozenset({
        "get_learner_context",
        "retrieve_content",
        "remember_about_learner",
        "schedule_review",
    }),
}


def surface_for_state(state: Dict[str, Any]) -> str:
    """Infer structural surface from state."""
    if state.get("is_coach"):
        return "coach"
    classroom = (state.get("classroom_surface") or "").strip().lower()
    if classroom in ("lesson", "doubt", "assess"):
        return f"classroom_{classroom}"
    if state.get("study_session_id") or state.get("surface") == "study":
        stage = (state.get("current_phase") or state.get("study_phase") or state.get("study_stage") or "").lower()
        if "practice" in stage or "assess" in stage:
            return "study_practice"
        return "study_learn"
    return "chat"


# ── Toolbelt ──────────────────────────────────────────────────────────────────


class Toolbelt:
    """A scoped, context-bound set of tools ready to hand to a model."""

    def __init__(
        self,
        specs: Iterable[ToolSpec],
        ctx: ToolContext,
        *,
        allowed_scopes: Iterable[str] = TUTOR_SCOPES,
        surface: Optional[str] = None,
    ) -> None:
        self.ctx = ctx
        self.allowed_scopes = frozenset(allowed_scopes)
        if surface is not None:
            self.surface = surface
        elif ctx.state.get("is_coach") or self.allowed_scopes == frozenset({"read", "write:schedule", "write:memory"}):
            self.surface = "coach"
        else:
            self.surface = surface_for_state(ctx.state)
        self._specs: Dict[str, ToolSpec] = {}
        self._failures: Dict[str, int] = {}
        self._candidates = list(specs)
        self._bind_specs()

    def _bind_specs(self) -> None:
        self.surface = surface_for_state(self.ctx.state) if self.ctx.state.get("study_session_id") or self.ctx.state.get("current_phase") else self.surface
        all_specs = self._candidates
        filter_surface = self.surface in SURFACE_TOOL_SETS and len(all_specs) > 15
        allowed_names = SURFACE_TOOL_SETS.get(self.surface) if filter_surface else None
        specs: Dict[str, ToolSpec] = {}
        for spec in all_specs:
            if allowed_names is not None and spec.name not in allowed_names:
                continue
            if spec.scopes <= self.allowed_scopes:
                specs[spec.name] = spec
            else:
                logger.debug(
                    "Tool %s withheld: needs %s, caller has %s",
                    spec.name,
                    sorted(spec.scopes - self.allowed_scopes),
                    sorted(self.allowed_scopes),
                )
        self._specs = specs

    def refresh(self) -> None:
        """Rebuild the offered tools after a phase change in this same turn."""
        self._bind_specs()

    def get_max_result_chars(self, name: str) -> int:
        spec = self._specs.get(name)
        return spec.max_result_chars if spec else 2500

    def get_spec(self, name: str) -> Optional[ToolSpec]:
        return self._specs.get(name)

    # ── introspection ────────────────────────────────────────────────────────

    @property
    def names(self) -> List[str]:
        return sorted(self._specs)

    def __contains__(self, name: str) -> bool:
        return name in self._specs

    def __len__(self) -> int:
        return len(self._specs)

    def schemas(self) -> List[Dict[str, Any]]:
        return [
            self._specs[n].schema()
            for n in self.names
            if self._failures.get(n, 0) < 2
        ]

    # ── dispatch ─────────────────────────────────────────────────────────────

    def call(self, name: str, raw_args: Any) -> ToolResult:
        """Validate and run one tool call. Never raises.

        Reports start/finish to services.progress so a streaming turn can show
        the student what the tutor is doing right now.
        """
        from services import progress

        if progress.is_cancelled():
            return ToolResult(ok=False, error="The student stopped this turn. Do not continue.")
        progress.report("tool", name, "start")
        result = self._call(name, raw_args)
        progress.report("tool", name, "done" if result.ok else "error")
        return result

    def _call(self, name: str, raw_args: Any) -> ToolResult:
        started = time.time()
        spec = self._specs.get(name)

        if spec is None:
            # Wrong name, or a tool this caller isn't scoped for. Tell the model
            # what it *can* call so its next attempt can be right.
            known = ", ".join(self.names) or "(none)"
            result = ToolResult(
                ok=False,
                error=f"No tool named '{name}' is available to you. Available tools: {known}.",
            )
            self._record(name, None, result, started)
            return result

        if self._failures.get(name, 0) >= 2:
            result = ToolResult(
                ok=False,
                error=f"Tool '{name}' failed repeatedly this turn, do not call it again.",
            )
            self._record(name, None, result, started)
            return result

        parsed_args = self._parse_args(raw_args)
        if isinstance(parsed_args, ToolResult):       # parse failure
            self._record(name, None, parsed_args, started)
            return parsed_args

        try:
            args = spec.args_model(**parsed_args)
        except ValidationError as exc:
            result = ToolResult(
                ok=False,
                error=f"Invalid arguments for '{name}': {_format_validation_error(exc)}",
            )
            self._record(name, parsed_args, result, started)
            return result

        # Repeating an identical read is always a mistake: either the model lost
        # track, or it is hoping for a different answer from a deterministic
        # lookup. Serving the cached result costs nothing and says so plainly,
        # which is what actually breaks the loop — a silent repeat just burns
        # another step of the student's budget. Only ever applied to reads;
        # a write must happen every time it is asked for.
        cache_key = ""
        is_cacheable = spec.scopes <= frozenset({SCOPE_READ}) and not spec.consequential
        if is_cacheable:
            cache_key = f"{name}:{json.dumps(parsed_args, sort_keys=True, default=str)}"
            cached = self.ctx._read_cache.get(cache_key)
            if cached is not None:
                if not cached.ok:
                    self._failures[name] = self._failures.get(name, 0) + 1
                repeat = ToolResult(
                    ok=cached.ok,
                    data=(
                        {
                            "repeated_call": (
                                "You already called this with these exact arguments "
                                "this turn. Here is the same result. Do something "
                                "different with it, or answer the student."
                            ),
                            **(cached.data if isinstance(cached.data, dict) else {"result": cached.data}),
                        }
                        if cached.ok
                        else None
                    ),
                    error=cached.error,
                )
                self._record(name, parsed_args, repeat, started)
                return repeat

        if spec.consequential and name not in self.ctx.granted_permissions:
            request = {
                "action": name,
                "args": parsed_args,
                "reason": spec.description.strip().splitlines()[0],
            }
            self.ctx.pending_permissions.append(request)
            result = ToolResult(ok=False, permission_required=request)
            self._record(name, parsed_args, result, started)
            return result

        timeout_s = getattr(spec, "timeout_s", 8.0)
        if self.ctx.budget is not None:
            timeout_s = min(timeout_s, self.ctx.budget.remaining())
        if timeout_s <= 0:
            result = ToolResult(ok=False, error=f"Tool '{name}' could not run: turn budget exhausted.")
            self._record(name, parsed_args, result, started)
            return result
        try:
            # ThreadPoolExecutor does not carry ContextVars automatically. Keep
            # cancellation, model budgets, usage accounting and SSE reporters
            # attached to this turn when a tool delegates or calls a model.
            token = self.ctx.open_call()
            context = contextvars.copy_context()

            def _invoke() -> Any:
                _CALL_TOKEN.set(token)
                return spec.handler(args, self.ctx)

            future = _TOOL_POOL.submit(context.run, _invoke)
            try:
                data = future.result(timeout=timeout_s)
            finally:
                self.ctx.seal_call(token)
            if isinstance(data, ToolResult):
                result = data
            elif isinstance(data, dict) and data.get("error"):
                result = ToolResult(ok=False, data=data, error=str(data["error"]))
            else:
                result = ToolResult(ok=True, data=data)
        except concurrent.futures.TimeoutError:
            self.ctx.seal_call(token)
            logger.warning("Tool %s timed out after %.1fs", name, timeout_s)
            result = ToolResult(
                ok=False,
                error=f"Tool '{name}' timed out after {timeout_s:.0f}s, try a simpler call or answer without it.",
            )
        except Exception as exc:  # noqa: BLE001 — deliberate: never break the loop
            logger.exception("Tool %s failed", name)
            result = ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        if not result.ok:
            self._failures[name] = self._failures.get(name, 0) + 1

        if is_cacheable and result.ok:
            self.ctx._read_cache[cache_key] = result

        if hasattr(self.ctx, "full_tool_results"):
            self.ctx.full_tool_results.append({
                "tool": name,
                "args": parsed_args,
                "result": result,
                "time": started,
            })

        self._record(name, parsed_args, result, started)
        return result

    # ── internals ────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_args(raw_args: Any) -> Any:
        """Accept a dict or a JSON string; report bad JSON back to the model."""
        if raw_args is None or raw_args == "":
            return {}
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                return ToolResult(
                    ok=False,
                    error=(
                        f"Arguments were not valid JSON ({exc.msg}). "
                        "Send a JSON object matching the tool's parameter schema."
                    ),
                )
            if not isinstance(parsed, dict):
                return ToolResult(
                    ok=False, error="Arguments must be a JSON object, not a bare value."
                )
            return parsed
        return ToolResult(ok=False, error=f"Unsupported argument type: {type(raw_args).__name__}")

    def _record(
        self, name: str, args: Optional[Dict[str, Any]], result: ToolResult, started: float
    ) -> None:
        preview: Any = None
        if result.ok:
            preview = result.data
        elif result.permission_required:
            preview = "permission_required"
        self.ctx.trace.add(
            TraceStep(
                kind="tool",
                name=name,
                args=args,
                ok=result.ok,
                error=result.error,
                result_preview=preview,
                duration_ms=int((time.time() - started) * 1000),
                depth=self.ctx.depth,
            )
        )


def _format_validation_error(exc: ValidationError) -> str:
    """Turn a pydantic error into one line a model can act on."""
    parts = []
    for err in exc.errors()[:4]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)
