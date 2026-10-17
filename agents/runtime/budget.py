"""Turn budget: deadlines and resource bounds for one student turn.

Enforces wall-clock limits and model-call caps across the reasoning loop,
expert delegations, and the answer checker.
"""

from __future__ import annotations

import contextvars
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("jee_tutor.budget")


class BudgetExhausted(Exception):
    """Raised when an LLM call or turn exceeds its budget deadline."""
    pass


DEFAULT_TUTOR_STEP_TIMEOUT_S = 25.0
DEFAULT_CHECKER_TIMEOUT_S = 15.0
DEFAULT_BACKGROUND_TIMEOUT_S = 60.0
DEFAULT_CLOSE_RESERVE_S = 8.0
DEFAULT_MAX_LLM_CALLS = 8
DEFAULT_MAX_INPUT_TOKENS = 100_000


@dataclass
class TurnBudget:
    """Carried on ToolContext and contextvar to enforce turn-level deadlines."""

    deadline: float                      # epoch timestamp when the turn expires
    max_llm_calls: int = DEFAULT_MAX_LLM_CALLS
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS
    close_reserve_s: float = DEFAULT_CLOSE_RESERVE_S
    llm_calls: int = 0
    input_tokens: int = 0

    @classmethod
    def from_duration(
        cls,
        duration_s: float,
        max_llm_calls: int = DEFAULT_MAX_LLM_CALLS,
        max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
        close_reserve_s: float = DEFAULT_CLOSE_RESERVE_S,
    ) -> TurnBudget:
        return cls(
            deadline=time.time() + duration_s,
            max_llm_calls=max_llm_calls,
            max_input_tokens=max_input_tokens,
            close_reserve_s=close_reserve_s,
        )

    def remaining(self, now: Optional[float] = None) -> float:
        """Remaining seconds before deadline."""
        t = now if now is not None else time.time()
        return max(0.0, self.deadline - t)

    def is_exhausted(self, now: Optional[float] = None) -> bool:
        """True when deadline has passed or LLM call cap reached."""
        return self.remaining(now) <= 0.0 or self.llm_calls >= self.max_llm_calls

    def timeout_for(
        self,
        per_call_cap: float = DEFAULT_TUTOR_STEP_TIMEOUT_S,
        reserve_s: float = 0.0,
    ) -> float:
        """Calculate per-request timeout as min(remaining - reserve, per_call_cap)."""
        if self.llm_calls >= self.max_llm_calls:
            raise BudgetExhausted(f"LLM call cap reached ({self.llm_calls}/{self.max_llm_calls})")
        rem = self.remaining() - reserve_s
        if rem <= 0.0:
            raise BudgetExhausted(f"Turn budget deadline exceeded (remaining: {rem:.2f}s)")
        return min(rem, per_call_cap)

    def child_budget(
        self,
        own_budget_s: float,
        reserve_s: float = DEFAULT_CLOSE_RESERVE_S,
        max_llm_calls: Optional[int] = None,
    ) -> TurnBudget:
        """Create a child budget bounded by min(own budget, parent remaining - reserve)."""
        rem_for_child = max(0.0, self.remaining() - reserve_s)
        child_duration = min(own_budget_s, rem_for_child)
        calls = max_llm_calls if max_llm_calls is not None else max(1, self.max_llm_calls - self.llm_calls)
        return TurnBudget(
            deadline=time.time() + child_duration,
            max_llm_calls=calls,
            max_input_tokens=self.max_input_tokens,
            close_reserve_s=min(2.0, reserve_s),
        )

    def record_call(self, tokens: int = 0) -> None:
        self.llm_calls += 1
        self.input_tokens += tokens


current_budget_var: contextvars.ContextVar[Optional[TurnBudget]] = contextvars.ContextVar(
    "current_budget_var", default=None
)
