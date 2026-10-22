"""
All Supabase read/write operations for the JEE Tutor Agent.

Single SupabaseClient instance is created once in this module and imported
by every agent that needs DB access.  No agent ever calls supabase-py directly.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from supabase import create_client, Client

import config


def _make_redis_client():
    """Connect the optional prefetch cache, or return None.

    Never raises. The cache is a latency optimisation and nothing depends on it,
    so an unset URL, a missing package and an unreachable server are all the
    same answer: no cache, carry on.
    """
    url = getattr(config, "REDIS_URL", "")
    if not url:
        return None
    try:
        import redis  # imported lazily: not needed when the cache is off

        client = redis.Redis.from_url(url, decode_responses=True)
        client.ping()
        logging.getLogger("jee_tutor.db").info("Prefetch cache connected.")
        return client
    except Exception as exc:
        logging.getLogger("jee_tutor.db").warning(
            "Prefetch cache unavailable (%s) — continuing without it.", str(exc)[:160]
        )
        return None


class SupabaseClient:
    def __init__(
        self,
        url: str,
        key: str,
        retrieval_url: Optional[str] = None,
        retrieval_key: Optional[str] = None,
    ) -> None:
        self.client: Client = create_client(url, key)
        if retrieval_url and retrieval_key and (retrieval_url != url or retrieval_key != key):
            self.retrieval_client: Client = create_client(retrieval_url, retrieval_key)
        else:
            self.retrieval_client: Client = self.client
        # None = not probed yet; set by memory_enabled() on first use.
        self._memory_enabled: Optional[bool] = None
        # None = not probed yet; set by episodes_enabled() on first use.
        # Independent of _memory_enabled: a missing learner_memory table must
        # not disable episodes, and a missing episode table must not disable facts.
        self._episodes_enabled: Optional[bool] = None
        # Set to True the first time chat_turns rejects a post-007 agent label.
        self._agent_used_is_narrow: bool = False
        # Optional prefetch cache. None when REDIS_URL is unset or unreachable;
        # callers must treat it as "may not be there" (`if db.redis:`), which is
        # how api_server.py already uses it.
        self.redis = _make_redis_client()

    # ─────────────────────────── users ────────────────────────────────────────

    def read_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        res = (
            self.client.table("users")
            .select("user_id, target_exam, preferred_depth")
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        return res.data

    def read_user_learning_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch the user's global learning profile from user_learning_profile table.
        This provides learning_velocity, preferred_content_type, and weak/strong chapters.
        """
        res = (
            self.client.table("user_learning_profile")
            .select("*")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    def create_user(
        self,
        name: str,
        target_exam: str,
        preferred_depth: str = "standard",
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        row = {
            "user_id": user_id or str(uuid.uuid4()),
            "name": name,
            "target_exam": target_exam,
            "preferred_depth": preferred_depth,
        }
        res = self.client.table("users").insert(row).execute()
        return res.data[0]

    # ─────────────────────────── sessions ─────────────────────────────────────

    def read_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        res = (
            self.client.table("sessions")
            .select("session_id, user_id, interaction_count, session_summary, active_quiz")
            .eq("session_id", session_id)
            .single()
            .execute()
        )
        return res.data

    def is_session_deleted(self, session_id: str) -> bool:
        try:
            res = self.client.table("sessions").select("deleted_at").eq("session_id", session_id).limit(1).execute()
        except Exception:  # migration 015 not applied: nothing can be deleted
            return False
        return bool(res.data and res.data[0].get("deleted_at"))

    def list_user_sessions(
        self, user_id: str, limit: int = 20, kind: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        def query(columns: str, migrated: bool):
            q = self.client.table("sessions").select(columns).eq("user_id", user_id)
            if migrated:
                q = q.is_("deleted_at", "null")
                if kind:
                    q = q.eq("kind", kind)
            return q.order("updated_at", desc=True).limit(limit).execute().data or []

        base = "session_id, user_id, interaction_count, session_summary, updated_at"
        try:
            return query(base + ", kind, title", True)
        except Exception:
            # Migration 015 not applied: no kind/title columns, so no filter either.
            logging.getLogger("jee_tutor.db").debug("sessions.kind/title unavailable; listing unfiltered", exc_info=True)
            return query(base, False)

    def update_session_title(self, session_id: str, title: Optional[str]) -> None:
        self.client.table("sessions").update({"title": title}).eq("session_id", session_id).execute()

    def delete_session(self, session_id: str) -> None:
        """Soft delete: hidden from every list and from history (migration 015)."""
        self.client.table("sessions").update(
            {"deleted_at": datetime.now(timezone.utc).isoformat()}
        ).eq("session_id", session_id).execute()

    def first_user_turn_preview(self, session_id: str, max_len: int = 80) -> Optional[str]:
        try:
            res = (
                self.client.table("chat_turns")
                .select("content")
                .eq("session_id", session_id)
                .eq("role", "user")
                .order("turn_index")
                .limit(1)
                .execute()
            )
        except Exception:
            return None
        if not res.data:
            return None
        content = (res.data[0].get("content") or "").strip()
        if not content:
            return None
        if len(content) <= max_len:
            return content
        return content[: max_len - 1] + "…"

    def first_user_turn_previews(
        self, session_ids: List[str], max_len: int = 80
    ) -> Dict[str, str]:
        """Batch first-user-turn previews for a session list (avoids N+1)."""
        if not session_ids:
            return {}
        try:
            res = (
                self.client.table("chat_turns")
                .select("session_id, content, turn_index")
                .in_("session_id", session_ids)
                .eq("role", "user")
                .order("turn_index")
                .execute()
            )
        except Exception:
            return {}
        previews: Dict[str, str] = {}
        for row in res.data or []:
            sid = row.get("session_id")
            if not sid or sid in previews:
                continue
            content = (row.get("content") or "").strip()
            if not content:
                continue
            previews[sid] = (
                content if len(content) <= max_len else content[: max_len - 1] + "…"
            )
        return previews

    def create_session(self, user_id: str, kind: Optional[str] = None) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "user_id": user_id,
            "interaction_count": 0,
            "active_quiz": False,
        }
        if kind:
            try:
                res = self.client.table("sessions").insert({**row, "kind": kind}).execute()
                return res.data[0]
            except Exception:
                # Migration 015 not applied: the session still works, it just is not tagged.
                logging.getLogger("jee_tutor.db").debug("sessions.kind unavailable; creating untagged session", exc_info=True)
        res = self.client.table("sessions").insert(row).execute()
        return res.data[0]

    def update_session_interaction_count(self, session_id: str, count: int) -> None:
        self.client.table("sessions").update(
            {"interaction_count": count, "updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("session_id", session_id).execute()

    def update_session_active_quiz(self, session_id: str, active: bool) -> None:
        self.client.table("sessions").update(
            {"active_quiz": active, "updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("session_id", session_id).execute()

    def update_session_summary(self, session_id: str, summary: str) -> None:
        self.client.table("sessions").update(
            {
                "session_summary": summary,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("session_id", session_id).execute()

    def read_session_summary(self, session_id: str) -> Optional[str]:
        res = (
            self.client.table("sessions")
            .select("session_summary")
            .eq("session_id", session_id)
            .single()
            .execute()
        )
        if res.data:
            return res.data.get("session_summary")
        return None

    def read_prior_session_summary(
        self, user_id: str, exclude_session_id: str
    ) -> Optional[str]:
        """
        Return the most recent session_summary for this user across all sessions
        *except* the current one.  Used by the Orchestrator on the first turn of
        a new session to inject continuity into downstream prompts.
        """
        res = (
            self.client.table("sessions")
            .select("session_summary")
            .eq("user_id", user_id)
            .neq("session_id", exclude_session_id)
            .not_.is_("session_summary", "null")
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if rows:
            return rows[0].get("session_summary")
        return None

    # ── Content Gap Events (Phase 2, Task 1.4) ───────────────────────────────
    def insert_content_gap_event(
        self,
        user_id: str,
        session_id: str,
        concept_id: str,
        query_text: str,
        top_score: float,
    ) -> None:
        try:
            self.client.table("content_gap_events").insert(
                {
                    "user_id": user_id,
                    "session_id": session_id,
                    "concept_id": concept_id,
                    "query_text": query_text,
                    "top_score": top_score,
                }
            ).execute()
        except Exception:
            # Fire-and-forget: do not fail the chat turn if logging fails
            pass

    # ─────────────────────────── chat_turns ───────────────────────────────────
    def read_recent_chat_turns(
        self, session_id: str, n: int = 2
    ) -> List[Dict[str, Any]]:
        res = (
            self.client.table("chat_turns")
            .select("role, content, turn_index, agent_used, concepts_referenced")
            .eq("session_id", session_id)
            .order("turn_index", desc=True)
            .limit(n)
            .execute()
        )
        return list(reversed(res.data or []))

    def assistant_turn_contents(self, session_id: str, turn_index: int) -> List[str]:
        """Assistant texts already stored for this session turn, if any."""
        res = (
            self.client.table("chat_turns")
            .select("content")
            .eq("session_id", session_id)
            .eq("turn_index", turn_index)
            .eq("role", "assistant")
            .execute()
        )
        return [str((row or {}).get("content") or "") for row in (res.data or [])]

    def read_all_chat_turns(self, session_id: str) -> List[Dict[str, Any]]:
        res = (
            self.client.table("chat_turns")
            .select(
                "turn_id, role, content, turn_index, is_summarized, agent_used, presentation"
            )
            .eq("session_id", session_id)
            .order("turn_index")
            .execute()
        )
        return res.data or []

    def read_unsummarized_turns(self, session_id: str) -> List[Dict[str, Any]]:
        res = (
            self.client.table("chat_turns")
            .select("turn_id, role, content, turn_index, agent_used")
            .eq("session_id", session_id)
            .eq("is_summarized", False)
            .order("turn_index")
            .execute()
        )
        return res.data or []

    #: Semantic labels mapped onto ones the PRE-007 check constraint accepts.
    #:
    #: `chat_turns.agent_used` is constrained to an enumeration of the old graph
    #: nodes. Migration 007 widens it to cover the Learning Agent. Rather than
    #: hardcode which schema we are on — and get it wrong on one of the two —
    #: inserts use the correct semantic label, and if the constraint rejects it
    #: we learn that once, fall back, and use this map for the rest of the
    #: process. After 007 is applied the map is simply never consulted.
    #:
    #: Centralised here because api_server.py kept its own copy of this list,
    #: which had already drifted out of sync with the labels actually in use.
    _AGENT_USED_COMPAT = {
        "student": "orchestrator",
        "learning_agent": "tutor",
        "graded_fast_path": "diagnostic",
        "answer_checker": "tutor",
        "practice_generator": "solver",
        "context_manager": "orchestrator",
    }

    #: Accepted by the constraint both before and after migration 007.
    _AGENT_USED_SAFE_DEFAULT = "tutor"

    def normalize_agent_used(self, agent_used: Optional[str]) -> Optional[str]:
        """Map an agent label onto one this database's constraint will accept.

        Returns the label unchanged until we have evidence the constraint is the
        narrow pre-007 one.
        """
        if not agent_used:
            return None
        if not self._agent_used_is_narrow:
            return agent_used
        return self._AGENT_USED_COMPAT.get(agent_used, self._AGENT_USED_SAFE_DEFAULT)

    @staticmethod
    def _is_agent_used_violation(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "chat_turns_agent_used_check" in msg

    def insert_chat_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        turn_index: int,
        agent_used: Optional[str] = None,
        concepts_referenced: Optional[List[str]] = None,
        presentation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "turn_index": turn_index,
            "is_summarized": False,
        }
        normalized = self.normalize_agent_used(agent_used)
        if normalized:
            row["agent_used"] = normalized
        if concepts_referenced:
            row["concepts_referenced"] = concepts_referenced
        if presentation is not None:
            row["presentation"] = presentation

        try:
            res = self.client.table("chat_turns").insert(row).execute()
        except Exception as exc:
            if not (agent_used and self._is_agent_used_violation(exc)):
                raise
            # This database still has the narrow pre-007 constraint. Learn that
            # once, then map every later label without a failed round-trip. A
            # label mismatch must never cost a student their turn.
            self._agent_used_is_narrow = True
            row["agent_used"] = self.normalize_agent_used(agent_used)
            res = self.client.table("chat_turns").insert(row).execute()
        return res.data[0]

    def update_chat_turn_presentation(
        self,
        session_id: str,
        turn_index: int,
        presentation: Dict[str, Any],
    ) -> bool:
        """
        Upsert the rich UI blob for an assistant turn (slides, flashcards, etc.).

        Presentation is written after the LangGraph turn completes; assistant
        `chat_turns` rows are inserted earlier inside agent nodes.
        Returns True if at least one row was updated.
        """
        res = (
            self.client.table("chat_turns")
            .update({"presentation": presentation})
            .eq("session_id", session_id)
            .eq("turn_index", turn_index)
            .eq("role", "assistant")
            .execute()
        )
        return bool(res.data)

    def mark_turns_summarized(self, turn_ids: List[str]) -> None:
        if not turn_ids:
            return
        self.client.table("chat_turns").update({"is_summarized": True}).in_(
            "turn_id", turn_ids
        ).execute()

    def count_socratic_turns_for_concept(
        self, session_id: str, concept_id: str
    ) -> int:
        """Count how many turns in this session used the socratic agent for this concept."""
        res = (
            self.client.table("chat_turns")
            .select("turn_id")
            .eq("session_id", session_id)
            .eq("agent_used", "socratic")
            .contains("concepts_referenced", [concept_id])
            .execute()
        )
        return len(res.data or [])

    # ─────────────────────────── concepts ─────────────────────────────────────

    def read_concepts(self) -> List[Dict[str, Any]]:
        """Return all 33 concepts as a flat list (concept_id, concept_name, chapter)."""
        res = (
            self.retrieval_client.table("concepts")
            .select("concept_id, concept_name, chapter, subject")
            .execute()
        )
        return res.data or []

    # ─────────────────────────── content ──────────────────────────────────────

    def read_content_by_ids(self, content_ids: List[str]) -> List[Dict[str, Any]]:
        if not content_ids:
            return []
        res = (
            self.retrieval_client.table("content")
            .select(
                "content_id, content_type, chapter, difficulty, core_text, solution, "
                "latex_formulae_core, latex_formulae_solution, "
                "has_image_core, has_image_solution, image_filename, "
                "diagram_description_core, primary_concept_id, secondary_concepts, "
                "prerequisite_concepts, question_type, target_exam"
            )
            .in_("content_id", content_ids)
            .execute()
        )
        return res.data or []

    # ─────────────────────────── user_concept_state ───────────────────────────

    def read_user_concept_state(
        self, user_id: str, concept_id: str
    ) -> Optional[Dict[str, Any]]:
        res = (
            self.client.table("user_concept_state")
            .select("*")
            .eq("user_id", user_id)
            .eq("concept_id", concept_id)
            .single()
            .execute()
        )
        return res.data

    def read_all_user_concept_states(self, user_id: str) -> List[Dict[str, Any]]:
        res = (
            self.client.table("user_concept_state")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )
        return res.data or []

    def read_weak_user_concept_states(self, user_id: str) -> List[Dict[str, Any]]:
        """Return all concepts where flagged_weak=True, ordered by struggle_streak desc."""
        res = (
            self.client.table("user_concept_state")
            .select("*")
            .eq("user_id", user_id)
            .eq("flagged_weak", True)
            .order("struggle_streak", desc=True)
            .execute()
        )
        return res.data or []

    def update_user_concept_state(
        self, user_id: str, concept_id: str, updates: Dict[str, Any]
    ) -> None:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        updates["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        self.client.table("user_concept_state").update(updates).eq(
            "user_id", user_id
        ).eq("concept_id", concept_id).execute()

    def batch_create_user_concept_states(
        self, user_id: str, concepts: List[Dict[str, Any]]
    ) -> None:
        """Called once at user registration to create 33 rows."""
        rows = [
            {
                "user_id": user_id,
                "concept_id": c["concept_id"],
                "subject": c.get("subject", "Physics"),
                "chapter": c.get("chapter"),
                "proficiency_score": 0.5,
                "attempts": 0,
                "correct": 0,
                "flagged_weak": False,
                "struggle_streak": 0,
                "error_type_dist": {
                    "conceptual": 0,
                    "procedural": 0,
                    "calculation": 0,
                    "misread": 0,
                },
                "avg_time_seconds": 0.0,
                "is_cold_start": True,
            }
            for c in concepts
        ]
        self.client.table("user_concept_state").insert(rows).execute()

    # ─────────────────────────── user_attempts ────────────────────────────────

    def insert_user_attempt(
        self,
        user_id: str,
        session_id: str,
        concept_id: str,
        content_id: Optional[str],
        is_correct: bool,
        error_type: Optional[str],
        time_taken_sec: Optional[int],
        # Optional: two graders (evaluate_student_proficiency, practice_analyzer)
        # never passed it, so their attempts raised TypeError and were lost.
        student_answer: Optional[str] = None,
        root_cause_concept_id: Optional[str] = None,
        *,
        source: Optional[str] = None,
        proficiency_before: Optional[float] = None,
        proficiency_after: Optional[float] = None,
        difficulty: Optional[int] = None,
        study_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "user_id": user_id,
            "session_id": session_id,
            "concept_id": concept_id,
            "is_correct": is_correct,
        }
        if root_cause_concept_id:
            row["root_cause_concept_id"] = root_cause_concept_id
        if content_id:
            row["content_id"] = content_id
        if error_type:
            row["error_type"] = error_type
        if time_taken_sec is not None:
            row["time_taken_sec"] = time_taken_sec
        if student_answer:
            row["student_answer"] = student_answer
        res = self.client.table("user_attempts").insert(row).execute()
        # Every graded path ends here, so this is where the ledger hears about it.
        self.insert_learning_event(
            user_id,
            "attempt",
            source=source or ("study" if study_session_id else "chat"),
            concept_id=concept_id,
            content_id=content_id,
            session_id=session_id,
            study_session_id=study_session_id,
            outcome="correct" if is_correct else "wrong",
            error_type=None if is_correct else error_type,
            difficulty=difficulty,
            time_taken_sec=time_taken_sec,
            proficiency_before=proficiency_before,
            proficiency_after=proficiency_after,
            payload={"student_answer": (student_answer or "")[:500]} if student_answer else None,
        )
        return res.data[0]

    def commit_graded_attempt(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Atomic attempt, proficiency, ledger, and study update (migration 022).

        Raises when the function is missing or the database rejects the call.
        Callers must not fall back to insert_user_attempt.
        """
        res = self.client.rpc("commit_graded_attempt", {"p_request": request}).execute()
        data = res.data
        if isinstance(data, list):
            data = data[0] if data else {}
        if isinstance(data, str):
            data = json.loads(data)
        return data or {}

    # ─────────────────────────── learning ledger (migration 012) ──────────────

    def insert_learning_event(
        self,
        user_id: str,
        kind: str,
        *,
        source: str = "chat",
        concept_id: Optional[str] = None,
        content_id: Optional[str] = None,
        session_id: Optional[str] = None,
        study_session_id: Optional[str] = None,
        outcome: Optional[str] = None,
        error_type: Optional[str] = None,
        difficulty: Optional[int] = None,
        time_taken_sec: Optional[int] = None,
        proficiency_before: Optional[float] = None,
        proficiency_after: Optional[float] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append one row to learning_events. Never raises — progress tracking
        must not cost the student their turn."""
        row: Dict[str, Any] = {"user_id": str(user_id), "kind": kind, "source": source}
        optional = {
            "concept_id": concept_id,
            "content_id": content_id,
            "session_id": session_id,
            "study_session_id": study_session_id,
            "outcome": outcome,
            "error_type": error_type,
            "difficulty": difficulty,
            "time_taken_sec": time_taken_sec,
            "proficiency_before": None if proficiency_before is None else round(float(proficiency_before), 4),
            "proficiency_after": None if proficiency_after is None else round(float(proficiency_after), 4),
            "payload": payload,
        }
        row.update({k: v for k, v in optional.items() if v is not None})
        try:
            self.client.table("learning_events").insert(row).execute()
        except Exception:
            logging.getLogger("jee_tutor.db").debug("learning event insert failed", exc_info=True)

    def read_learning_events(
        self, user_id: str, *, since_iso: Optional[str] = None, limit: int = 5000
    ) -> List[Dict[str, Any]]:
        try:
            q = (
                self.client.table("learning_events")
                .select("*")
                .eq("user_id", str(user_id))
                .order("created_at", desc=True)
                .limit(limit)
            )
            if since_iso:
                q = q.gte("created_at", since_iso)
            return q.execute().data or []
        except Exception:
            logging.getLogger("jee_tutor.db").debug("learning events read failed", exc_info=True)
            return []

    def read_seen_content_ids(
        self, user_id: str, session_id: Optional[str] = None
    ) -> List[str]:
        """Content IDs already shown to the user (for Follow-Up deduplication)."""
        q = (
            self.client.table("user_attempts")
            .select("content_id")
            .eq("user_id", user_id)
            .not_.is_("content_id", "null")
        )
        if session_id:
            q = q.eq("session_id", session_id)
        res = q.execute()
        return [r["content_id"] for r in (res.data or []) if r.get("content_id")]

    def read_recent_attempts_by_root_cause(
        self, user_id: str, root_cause_concept_id: str, exclude_concept_id: str, limit: int = 1
    ) -> Optional[Dict[str, Any]]:
        """Find recent incorrect attempts that share this root cause but occurred on a different concept."""
        res = (
            self.client.table("user_attempts")
            .select("*, concept:concepts!user_attempts_concept_id_fkey(name)")
            .eq("user_id", user_id)
            .eq("root_cause_concept_id", root_cause_concept_id)
            .neq("concept_id", exclude_concept_id)
            .eq("is_correct", False)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        if res.data:
            return res.data[0]
        return None

    def read_recent_user_attempts(
        self, user_id: str, concept_id: str, n: int = 5
    ) -> List[Dict[str, Any]]:
        res = (
            self.client.table("user_attempts")
            .select("is_correct, error_type, time_taken_sec, created_at")
            .eq("user_id", user_id)
            .eq("concept_id", concept_id)
            .order("created_at", desc=True)
            .limit(n)
            .execute()
        )
        return res.data or []

    # ─────────────────────── learner memory (migration 006) ───────────────────
    #
    # Every method here degrades to a no-op / empty result when migration 006 has
    # not been applied yet, so the agent runs against an un-migrated database
    # without memory instead of crashing the turn. `memory_enabled()` reports
    # which of the two worlds we are in.

    _MISSING_TABLE_MARKERS = (
        "does not exist",
        "could not find the table",
        "pgrst205",
        "relation \"public.learner_memory\"",
        "schema cache",
    )

    def _is_missing_table(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(marker in msg for marker in self._MISSING_TABLE_MARKERS)

    def memory_enabled(self) -> bool:
        """True when migration 006 has been applied (learner_memory is queryable)."""
        if self._memory_enabled is None:
            try:
                self.client.table("learner_memory").select("memory_id").limit(1).execute()
                self._memory_enabled = True
            except Exception as exc:
                if self._is_missing_table(exc):
                    self._memory_enabled = False
                else:
                    # A transient error is not proof the table is absent — don't
                    # cache a false negative that disables memory for the process.
                    return False
        return self._memory_enabled

    #: Memory kinds ranked by how much they should influence teaching.
    #: Misconceptions must never fall out of the recall window just because
    #: newer context notes were written.  Within each kind, confidence desc.
    _MEMORY_KIND_PRIORITY = {
        "misconception": 0,
        "strength": 1,
        "goal": 2,
        "plan": 3,
        "preference": 4,
        "context": 5,
    }

    def read_learner_memories(
        self,
        user_id: str,
        *,
        query: Optional[str] = None,
        subject_key: Optional[str] = None,
        kinds: Optional[List[str]] = None,
        limit: int = 12,
        min_confidence: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """Active learner memories, ranked by kind priority then confidence.

        Kind priority: misconception > strength > goal > plan > preference > context.
        Within each kind, highest confidence first.  This ensures critical
        learning insights (misconceptions, strengths) survive as the table
        grows past ~50 entries per student, instead of being pushed out by
        more recent but less actionable context notes.

        Empty list if memory is off.
        """
        if not self.memory_enabled():
            return []
        try:
            # Fetch more than `limit` so we can re-rank client-side by kind
            # priority.  The DB sorts by confidence desc, which is the tiebreaker
            # within each kind after we re-rank.
            fetch_limit = max(limit * 3, 36)
            q = (
                self.client.table("learner_memory")
                .select("memory_id, kind, subject_key, fact, evidence, confidence, created_at, embedding")
                .eq("user_id", user_id)
                .is_("superseded_by", "null")
                .gte("confidence", min_confidence)
            )
            if subject_key:
                q = q.eq("subject_key", subject_key)
            if kinds:
                q = q.in_("kind", kinds)
            res = q.order("confidence", desc=True).limit(fetch_limit).execute()
            rows = res.data or []
            if not rows:
                return []

            # Re-rank: kind priority first, then confidence desc within kind.
            fallback_priority = len(self._MEMORY_KIND_PRIORITY)
            
            query_vec = None
            if query:
                try:
                    from retrieval import embedder
                    query_vec = embedder.encode_one(query)
                except Exception:
                    pass

            def _similarity(vec1, vec2):
                if not vec1 or not vec2 or len(vec1) != len(vec2):
                    return 0.0
                dot = sum(a * b for a, b in zip(vec1, vec2))
                norm1 = sum(a * a for a in vec1) ** 0.5
                norm2 = sum(b * b for b in vec2) ** 0.5
                if norm1 == 0 or norm2 == 0:
                    return 0.0
                return dot / (norm1 * norm2)

            rows.sort(
                key=lambda m: (
                    self._MEMORY_KIND_PRIORITY.get(m.get("kind", ""), fallback_priority),
                    -_similarity(query_vec, m.get("embedding")) if query_vec and m.get("embedding") else 0,
                    -(float(m.get("confidence") or 0)),
                )
            )
            # Remove embedding from output to save memory
            for r in rows:
                r.pop("embedding", None)
            return rows[:limit]
        except Exception:
            return []

    def insert_learner_memory(
        self,
        user_id: str,
        kind: str,
        fact: str,
        *,
        evidence: Optional[str] = None,
        subject_key: Optional[str] = None,
        confidence: float = 0.6,
        source: str = "tutor",
        session_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Write one atomic learner fact. Returns the row, or None if memory is off."""
        if not self.memory_enabled():
            return None
        row = {
            "user_id": user_id,
            "kind": kind,
            "fact": fact,
            "evidence": evidence,
            "subject_key": subject_key,
            "confidence": max(0.0, min(1.0, float(confidence))),
            "source": source,
            "session_id": session_id,
        }
        try:
            from retrieval import embedder
            vec = embedder.encode_one(fact)
            # A NumPy vector is never a boolean. `if vec` raises on an array.
            size = getattr(vec, "size", None) if vec is not None else 0
            if vec is not None and size != 0:
                row["embedding"] = [float(item) for item in list(vec)]
        except Exception:
            pass
        try:
            res = self.client.table("learner_memory").insert(row).execute()
            return (res.data or [None])[0]
        except Exception:
            return None

    def supersede_learner_memory(self, memory_id: str, replacement_id: Optional[str]) -> None:
        """Retire a memory that is now wrong, optionally pointing at what replaced it."""
        if not self.memory_enabled():
            return
        try:
            self.client.table("learner_memory").update(
                {
                    "superseded_by": replacement_id,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("memory_id", memory_id).execute()
        except Exception:
            pass

    def bump_learner_memory_hits(self, memory_ids: List[str]) -> None:
        """Record that these memories were recalled into a prompt."""
        if not memory_ids or not self.memory_enabled():
            return
        try:
            for mid in memory_ids:
                self.client.rpc(
                    "increment_learner_memory_hit", {"p_memory_id": mid}
                ).execute()
        except Exception:
            pass  # RPC is optional; hit_count is telemetry, never load-bearing.

    # ─────────────────────── review schedule (migration 006) ──────────────────

    def upsert_review(
        self,
        user_id: str,
        concept_id: str,
        due_at: str,
        *,
        reason: Optional[str] = None,
        created_by: str = "tutor",
        session_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Schedule (or re-schedule) one pending review for a concept."""
        if not self.memory_enabled():
            return None
        try:
            existing = (
                self.client.table("review_schedule")
                .select("review_id")
                .eq("user_id", user_id)
                .eq("concept_id", concept_id)
                .eq("status", "pending")
                .execute()
            )
            if existing.data:
                rid = existing.data[0]["review_id"]
                self.client.table("review_schedule").update(
                    {"due_at": due_at, "reason": reason}
                ).eq("review_id", rid).execute()
                return {"review_id": rid, "due_at": due_at, "rescheduled": True}
            res = (
                self.client.table("review_schedule")
                .insert(
                    {
                        "user_id": user_id,
                        "concept_id": concept_id,
                        "due_at": due_at,
                        "reason": reason,
                        "created_by": created_by,
                        "session_id": session_id,
                    }
                )
                .execute()
            )
            return (res.data or [None])[0]
        except Exception:
            return None

    def read_due_reviews(self, user_id: str, *, now_iso: Optional[str] = None) -> List[Dict[str, Any]]:
        """Pending reviews whose due_at has passed."""
        if not self.memory_enabled():
            return []
        cutoff = now_iso or datetime.now(timezone.utc).isoformat()
        try:
            res = (
                self.client.table("review_schedule")
                .select("review_id, concept_id, due_at, reason")
                .eq("user_id", user_id)
                .eq("status", "pending")
                .lte("due_at", cutoff)
                .order("due_at")
                .execute()
            )
            return res.data or []
        except Exception:
            return []

    def complete_review(self, review_id: str) -> None:
        if not self.memory_enabled():
            return
        try:
            self.client.table("review_schedule").update(
                {"status": "done", "completed_at": datetime.now(timezone.utc).isoformat()}
            ).eq("review_id", review_id).execute()
        except Exception:
            pass

    # ─────────────────────── agent trace (migration 006) ──────────────────────

    def insert_agent_trace(self, trace: Dict[str, Any]) -> None:
        """Persist one turn's decision trace. Never raises — telemetry only."""
        if not self.memory_enabled():
            return
        try:
            self.client.table("agent_trace").insert(trace).execute()
        except Exception:
            pass

    # ─────────────────────── study sessions (migration 010) ─────────────────

    def create_study_session(
        self,
        user_id: str,
        concept_id: str,
        concept_name: Optional[str] = None,
        chapter: Optional[str] = None,
        agent_session_id: Optional[str] = None,
        *,
        mode: str = "learn",
        question_source: str = "any",
        difficulty_level: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """Create a new Study Room session. A 'practice' session starts in practice."""
        row: Dict[str, Any] = {
            "user_id": user_id,
            "concept_id": concept_id,
            "current_phase": "practice" if mode == "practice" else "learn",
            "difficulty_level": max(1, min(5, int(difficulty_level or 1))),
        }
        if mode == "practice":
            row["mode"] = "practice"
        if question_source == "pyq":
            row["question_source"] = "pyq"
        if concept_name:
            row["concept_name"] = concept_name
        if chapter:
            row["chapter"] = chapter
        if agent_session_id:
            row["agent_session_id"] = agent_session_id
        try:
            res = self.client.table("study_sessions").insert(row).execute()
            return (res.data or [None])[0]
        except Exception:
            return None

    def read_study_session(self, study_session_id: str) -> Optional[Dict[str, Any]]:
        try:
            res = (
                self.client.table("study_sessions")
                .select("*")
                .eq("study_session_id", study_session_id)
                .single()
                .execute()
            )
            return res.data
        except Exception:
            return None

    def update_study_session_phase(
        self, study_session_id: str, phase: str, phase_summary: Optional[str] = None
    ) -> None:
        # A new phase starts its own outline position and turn count (migration 011).
        updates: Dict[str, Any] = {"current_phase": phase, "phase_step": 0, "phase_turns": 0}
        try:
            # Append to phase_history
            session = self.read_study_session(study_session_id)
            if session:
                history = session.get("phase_history") or []
                history.append({
                    "phase": phase,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "summary": phase_summary,
                })
                updates["phase_history"] = history
            self.client.table("study_sessions").update(updates).eq(
                "study_session_id", study_session_id
            ).execute()
        except Exception:
            pass

    def update_study_session_fields(self, study_session_id: str, fields: Dict[str, Any]) -> None:
        """Pacing fields from migration 011: lesson_plan, phase_step, phase_turns."""
        try:
            self.client.table("study_sessions").update(fields).eq(
                "study_session_id", study_session_id
            ).execute()
        except Exception:
            logging.getLogger("jee_tutor.db").debug("study session field update failed", exc_info=True)

    def update_study_session_stats(
        self, study_session_id: str, questions_served: int, questions_correct: int,
        difficulty_level: int
    ) -> None:
        try:
            self.client.table("study_sessions").update({
                "questions_served": questions_served,
                "questions_correct": questions_correct,
                "difficulty_level": difficulty_level,
            }).eq("study_session_id", study_session_id).execute()
        except Exception:
            pass

    def complete_study_session(self, study_session_id: str, outcome: str) -> None:
        try:
            self.client.table("study_sessions").update({
                "current_phase": "completed",
                "outcome": outcome,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("study_session_id", study_session_id).execute()
        except Exception:
            pass

    def list_user_study_sessions(
        self, user_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        try:
            res = (
                self.client.table("study_sessions")
                .select("study_session_id, concept_id, concept_name, chapter, "
                        "current_phase, questions_served, questions_correct, "
                        "difficulty_level, outcome, started_at, completed_at")
                .eq("user_id", user_id)
                .order("started_at", desc=True)
                .limit(limit)
                .execute()
            )
            return res.data or []
        except Exception:
            return []

    def read_study_sessions_for_concept(
        self, user_id: str, concept_id: str
    ) -> List[Dict[str, Any]]:
        try:
            res = (
                self.client.table("study_sessions")
                .select("*")
                .eq("user_id", user_id)
                .eq("concept_id", concept_id)
                .order("started_at", desc=True)
                .execute()
            )
            return res.data or []
        except Exception:
            return []

    # ─────────────────────── study session events ─────────────────────────────

    def insert_study_event(
        self,
        study_session_id: str,
        user_id: str,
        event_type: str,
        *,
        concept_id: Optional[str] = None,
        content_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        row: Dict[str, Any] = {
            "study_session_id": study_session_id,
            "user_id": user_id,
            "event_type": event_type,
        }
        if concept_id:
            row["concept_id"] = concept_id
        if content_id:
            row["content_id"] = content_id
        if payload:
            row["payload"] = payload
        try:
            res = self.client.table("study_session_events").insert(row).execute()
            return (res.data or [None])[0]
        except Exception:
            return None

    def read_study_events(
        self, study_session_id: str, event_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        try:
            q = (
                self.client.table("study_session_events")
                .select("*")
                .eq("study_session_id", study_session_id)
                .order("created_at")
            )
            if event_type:
                q = q.eq("event_type", event_type)
            res = q.execute()
            return res.data or []
        except Exception:
            return []

    def count_study_events(self, study_session_id: str, event_type: str) -> int:
        try:
            res = (
                self.client.table("study_session_events")
                .select("event_id")
                .eq("study_session_id", study_session_id)
                .eq("event_type", event_type)
                .execute()
            )
            return len(res.data or [])
        except Exception:
            return 0

    def read_questions_attempted(
        self, study_session_id: str
    ) -> List[Dict[str, Any]]:
        """Return all question_attempted events for a study session."""
        return self.read_study_events(study_session_id, "question_attempted")

    # ─────────────────────── learner readiness scores ─────────────────────────

    def upsert_readiness_score(
        self,
        user_id: str,
        concept_id: Optional[str],
        score: float,
        confidence: float = 0.5,
        factors: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        score = max(0.0, min(100.0, float(score)))
        confidence = max(0.0, min(1.0, float(confidence)))
        try:
            # Check if row exists
            q = (
                self.client.table("learner_readiness_scores")
                .select("score_id")
                .eq("user_id", user_id)
            )
            if concept_id:
                q = q.eq("concept_id", concept_id)
            else:
                q = q.is_("concept_id", "null")
            existing = q.execute()

            row = {
                "user_id": user_id,
                "concept_id": concept_id,
                "score": score,
                "confidence": confidence,
                "factors": factors or {},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if existing.data:
                sid = existing.data[0]["score_id"]
                self.client.table("learner_readiness_scores").update(row).eq(
                    "score_id", sid
                ).execute()
                row["score_id"] = sid
                return row
            res = self.client.table("learner_readiness_scores").insert(row).execute()
            return (res.data or [None])[0]
        except Exception:
            return None

    def read_readiness_scores(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            res = (
                self.client.table("learner_readiness_scores")
                .select("*")
                .eq("user_id", user_id)
                .order("updated_at", desc=True)
                .execute()
            )
            return res.data or []
        except Exception:
            return []

    def read_concept_readiness(
        self, user_id: str, concept_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            res = (
                self.client.table("learner_readiness_scores")
                .select("*")
                .eq("user_id", user_id)
                .eq("concept_id", concept_id)
                .single()
                .execute()
            )
            return res.data
        except Exception:
            return None

    # ─────────────────── episodic memory (migration 017) ─────────────────────
    #
    # Projection rows for the Memory Agent. Not learner_memory. Every method
    # fails open. episodes_enabled() is a separate probe from memory_enabled().

    def episodes_enabled(self) -> bool:
        """True when migration 017 has been applied (episodic_memory is queryable)."""
        if self._episodes_enabled is None:
            try:
                self.client.table("episodic_memory").select("episode_id").limit(1).execute()
                self._episodes_enabled = True
            except Exception as exc:
                if self._is_missing_table(exc):
                    self._episodes_enabled = False
                else:
                    # A transient error is not proof the table is absent — don't
                    # cache a false negative that disables episodes for the process.
                    return False
        return self._episodes_enabled

    def _column_enabled(self, cache_name: str, table: str, column: str) -> bool:
        """Probe one column. Cache False only when that column or table is absent.

        A connection error is not cached. The probe result never includes the
        database URL or key.
        """
        cached = getattr(self, cache_name, None)
        if cached is not None:
            return cached
        try:
            self.client.table(table).select(column).limit(1).execute()
        except Exception as exc:
            if self._is_missing_column(exc, column) or self._is_missing_table(exc):
                setattr(self, cache_name, False)
                return False
            return False
        setattr(self, cache_name, True)
        return True

    @staticmethod
    def _is_missing_column(exc: Exception, column: str) -> bool:
        msg = str(exc).lower()
        name = column.lower()
        if name not in msg:
            return False
        return any(marker in msg for marker in ("does not exist", "42703", "schema cache", "could not find"))

    def episode_evidence_enabled(self) -> bool:
        """True when migration 021 has added assessment provenance columns."""
        if not self.episodes_enabled():
            return False
        return self._column_enabled("_evidence_enabled", "episodic_memory", "assessment_status")

    def episode_approach_enabled(self) -> bool:
        """True when migration 018 has added episodic_memory.approach."""
        if not self.episodes_enabled():
            return False
        return self._column_enabled("_approach_enabled", "episodic_memory", "approach")

    def consolidation_schema_ready(self) -> bool:
        """True when 018's tracking column and table are both queryable."""
        if not self.episodes_enabled() or not self.memory_enabled():
            return False
        column = self._column_enabled("_consolidated_at_enabled", "episodic_memory", "consolidated_at")
        table = self._column_enabled("_consolidation_table_enabled", "episodic_consolidation", "cluster_key")
        return column and table

    def upsert_episode(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Insert or update one episode keyed by (session_id, assistant_turn_index).

        Returns the row, or None when the table is unavailable, the payload is
        incomplete, or the existing row belongs to a different user. Null
        feedback_* fields are omitted. A replay that sends feedback_status
        'pending' does not write that value, so a closed row stays closed.
        """
        if not self.episodes_enabled():
            return None
        try:
            if not self._episode_upsert_ready(row):
                return None
            payload = {
                key: value
                for key, value in row.items()
                if not (str(key).startswith("feedback_") and value is None)
            }
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            if not payload.get("approach") or not self.episode_approach_enabled():
                payload.pop("approach", None)
            existing = (
                self.client.table("episodic_memory")
                .select("user_id, feedback_status, feedback_outcome")
                .eq("session_id", payload["session_id"])
                .eq("assistant_turn_index", payload["assistant_turn_index"])
                .limit(1)
                .execute()
            )
            found = (existing.data or [None])[0]
            if found is not None and str(found.get("user_id")) != str(payload["user_id"]):
                logging.getLogger("jee_tutor.db").warning(
                    "Refusing episodic_memory upsert: existing row user_id differs "
                    "(session_id=%s assistant_turn_index=%s).",
                    payload.get("session_id"),
                    payload.get("assistant_turn_index"),
                )
                return None
            # Replay of a non-grade turn must not reopen a row that later
            # feedback already closed. On insert the column default is pending.
            if found is not None and payload.get("feedback_status") == "pending":
                payload.pop("feedback_status", None)
            # A replay that no longer carries the grade must not replace the
            # retrieval text that was refreshed when feedback closed the row.
            if (
                found is not None
                and found.get("feedback_status") == "next_attempt"
                and not payload.get("feedback_outcome")
            ):
                payload.pop("retrieval_text", None)
                payload.pop("embedding", None)
                payload.pop("embed_model", None)
            if not self.episode_evidence_enabled():
                for key in ("assessment_status", "evidence_source", "student_response", "interaction_id"):
                    payload.pop(key, None)
            # default_to_null=False keeps omitted columns instead of writing NULL.
            res = (
                self.client.table("episodic_memory")
                .upsert(
                    payload,
                    on_conflict="session_id,assistant_turn_index",
                    default_to_null=False,
                )
                .execute()
            )
            return (res.data or [None])[0]
        except Exception:
            return None

    @staticmethod
    def _episode_upsert_ready(row: Dict[str, Any]) -> bool:
        if not isinstance(row, dict):
            return False
        for key in ("user_id", "session_id", "surface", "route", "task_type", "retrieval_text"):
            if not row.get(key):
                return False
        return row.get("assistant_turn_index") is not None

    def close_pending_episode(
        self,
        *,
        user_id: str,
        concept_id: str,
        exclude_turn_index: int,
        content_id: Optional[str] = None,
        interaction_id: Optional[str] = None,
        is_correct: Optional[bool] = None,
        attempt_id: Optional[str] = None,
        proficiency_before: Optional[float] = None,
        proficiency_after: Optional[float] = None,
    ) -> Optional[str]:
        """Close the newest older pending episode for this user and concept.

        Returns that episode_id, or None when nothing pending matches. The row
        at exclude_turn_index is never updated. A content_id match wins over a
        newer concept-only row inside the same candidate set.
        """
        if not user_id or not concept_id or exclude_turn_index is None or not isinstance(is_correct, bool):
            return None
        if not self.episodes_enabled():
            return None
        try:
            # One graded attempt closes at most one prior episode. A retry
            # returns the row this attempt already closed.
            if attempt_id:
                prior = (
                    self.client.table("episodic_memory")
                    .select("episode_id")
                    .eq("user_id", user_id)
                    .eq("feedback_attempt_id", attempt_id)
                    .eq("feedback_status", "next_attempt")
                    .eq("status", "active")
                    .limit(1)
                    .execute()
                )
                already = (prior.data or [None])[0]
                if already and already.get("episode_id"):
                    return str(already["episode_id"])
            columns = (
                "episode_id, content_id, assistant_turn_index, created_at, "
                "proficiency_before, proficiency_after, session_id"
            )
            if self.episode_evidence_enabled():
                columns += ", interaction_id"
            res = (
                self.client.table("episodic_memory")
                .select(columns)
                .eq("user_id", user_id)
                .eq("concept_id", concept_id)
                .eq("status", "active")
                .eq("feedback_status", "pending")
                .neq("assistant_turn_index", exclude_turn_index)
                .order("created_at", desc=True)
                .execute()
            )
            rows = res.data or []
            chosen = None
            basis = "none"
            if interaction_id:
                for candidate in rows:
                    if candidate.get("interaction_id") == interaction_id:
                        chosen = candidate
                        basis = "interaction"
                        break
            if chosen is None and content_id:
                for candidate in rows:
                    if candidate.get("content_id") == content_id:
                        chosen = candidate
                        basis = "content"
                        break
            # Concept-only is a fallback used only when this grade names no question.
            # Historical rows have no interaction_id, so a computed id that matches
            # nothing must not block that fallback.
            if chosen is None and rows and not content_id:
                chosen = rows[0]
                basis = "concept"
            if chosen is None:
                return None
            logging.getLogger("jee_tutor.db").debug(
                "close_pending_episode basis=%s episode=%s", basis, chosen.get("episode_id")
            )
            episode_id = chosen.get("episode_id")
            if not episode_id or chosen.get("assistant_turn_index") == exclude_turn_index:
                return None
            now = datetime.now(timezone.utc).isoformat()
            patch: Dict[str, Any] = {
                "feedback_status": "next_attempt",
                "feedback_outcome": "correct" if is_correct else "incorrect",
                "feedback_attempt_id": attempt_id,
                "feedback_at": now,
                "updated_at": now,
            }
            if chosen.get("proficiency_before") is None and proficiency_before is not None:
                patch["proficiency_before"] = proficiency_before
            if chosen.get("proficiency_after") is None and proficiency_after is not None:
                patch["proficiency_after"] = proficiency_after
            updated = (
                self.client.table("episodic_memory")
                .update(patch)
                .eq("episode_id", episode_id)
                .eq("user_id", user_id)
                .eq("feedback_status", "pending")
                .execute()
            )
            if not updated.data:
                return None
            return str(episode_id)
        except Exception:
            return None

    def read_episode_retrieval_source(
        self, user_id: str, episode_id: str
    ) -> Optional[Dict[str, Any]]:
        """Projection fields the Memory Agent needs to rebuild retrieval text.

        Persistence only. Does not embed.
        """
        if not user_id or not episode_id or not self.episodes_enabled():
            return None
        try:
            res = (
                self.client.table("episodic_memory")
                .select(
                    "episode_id, user_id, surface, concept_id, task_type, "
                    "observed_outcome, query_excerpt, actions, feedback_outcome, "
                    "embedding, embed_model"
                    + (", approach" if self.episode_approach_enabled() else "")
                )
                .eq("user_id", user_id)
                .eq("episode_id", episode_id)
                .eq("status", "active")
                .limit(1)
                .execute()
            )
            row = (res.data or [None])[0]
            return row if isinstance(row, dict) else None
        except Exception:
            return None

    def update_episode_retrieval(
        self,
        *,
        user_id: str,
        episode_id: str,
        retrieval_text: str,
        embedding: Any = None,
        embed_model: Optional[str] = None,
    ) -> None:
        """Write retrieval fields for one episode. Does not touch feedback columns."""
        if not user_id or not episode_id or not retrieval_text or not self.episodes_enabled():
            return None
        try:
            self.client.table("episodic_memory").update(
                {
                    "retrieval_text": retrieval_text,
                    "embedding": embedding,
                    "embed_model": embed_model,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("episode_id", episode_id).eq("user_id", user_id).execute()
        except Exception:
            return None

    def fetch_episode_candidates(
        self, user_id: str, concept_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Active episodes for one user. No ranking — the Memory Agent scores them.

        Known concept: newest 24 on that concept, plus newest 16 on any concept,
        deduped by episode_id. Unknown concept: newest 40. Empty list on failure.
        """
        if not user_id or not self.episodes_enabled():
            return []
        columns = (
            "episode_id, user_id, concept_id, task_type, actions, query_excerpt, "
            "observed_outcome, checker_verdict, feedback_status, feedback_outcome, "
            "embedding, embed_model, created_at"
        )
        if self.episode_approach_enabled():
            columns += ", approach"
        try:
            if concept_id:
                concept_res = (
                    self.client.table("episodic_memory")
                    .select(columns)
                    .eq("user_id", user_id)
                    .eq("status", "active")
                    .eq("concept_id", concept_id)
                    .order("created_at", desc=True)
                    .limit(24)
                    .execute()
                )
                any_res = (
                    self.client.table("episodic_memory")
                    .select(columns)
                    .eq("user_id", user_id)
                    .eq("status", "active")
                    .order("created_at", desc=True)
                    .limit(16)
                    .execute()
                )
                merged = list(concept_res.data or []) + list(any_res.data or [])
            else:
                res = (
                    self.client.table("episodic_memory")
                    .select(columns)
                    .eq("user_id", user_id)
                    .eq("status", "active")
                    .order("created_at", desc=True)
                    .limit(40)
                    .execute()
                )
                merged = list(res.data or [])
            seen = set()
            unique: List[Dict[str, Any]] = []
            for item in merged:
                episode_id = item.get("episode_id")
                if episode_id in seen:
                    continue
                seen.add(episode_id)
                unique.append(item)
            return unique
        except Exception:
            return []

    def invalidate_session(self, session_id: str) -> None:
        """Mark every episode for a session not retrievable. Does not delete."""
        if not session_id or not self.episodes_enabled():
            return None
        try:
            self.client.table("episodic_memory").update(
                {
                    "status": "invalid",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("session_id", session_id).execute()
        except Exception:
            return None
        self.retire_facts_without_active_support(session_id=session_id)

    def invalidate_turn(self, session_id: str, assistant_turn_index: int) -> None:
        """Mark one assistant slot invalid. A missing row is a no-op."""
        if not session_id or assistant_turn_index is None or not self.episodes_enabled():
            return None
        try:
            (
                self.client.table("episodic_memory")
                .update(
                    {
                        "status": "invalid",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                .eq("session_id", session_id)
                .eq("assistant_turn_index", assistant_turn_index)
                .execute()
            )
        except Exception:
            return None
        self.retire_facts_without_active_support(session_id=session_id, assistant_turn_index=assistant_turn_index)

    def retire_facts_without_active_support(
        self,
        *,
        session_id: str,
        assistant_turn_index: Optional[int] = None,
    ) -> None:
        """Supersede a derived fact only when every supporting episode is inactive.

        Facts with no consolidation_memory_id link are left alone. Historical
        rows from before migration 018 have no such link.
        """
        if not session_id or not self.episodes_enabled() or not self.memory_enabled():
            return None
        try:
            q = (
                self.client.table("episodic_memory")
                .select("consolidation_memory_id")
                .eq("session_id", session_id)
                .eq("status", "invalid")
            )
            if assistant_turn_index is not None:
                q = q.eq("assistant_turn_index", assistant_turn_index)
            linked = q.limit(100).execute()
        except Exception:
            return None
        memory_ids = []
        for row in linked.data or []:
            memory_id = (row or {}).get("consolidation_memory_id")
            if memory_id and memory_id not in memory_ids:
                memory_ids.append(memory_id)
        for memory_id in memory_ids:
            try:
                active = (
                    self.client.table("episodic_memory")
                    .select("episode_id")
                    .eq("consolidation_memory_id", memory_id)
                    .eq("status", "active")
                    .limit(1)
                    .execute()
                )
            except Exception:
                continue
            if active.data:
                continue
            self.supersede_learner_memory(str(memory_id), None)

    def list_unconsolidated_episodes(self, user_id: str) -> List[Dict[str, Any]]:
        """Active episodes for one user that consolidation has not consumed.

        Empty when the schema is missing or the query fails. Never returns
        another user's rows.
        """
        if not user_id or not self.consolidation_schema_ready():
            return []
        # attempt ids, the student response, and the evidence source are what
        # eligible_clusters uses to decide a repeated mistake or a verified
        # strength. Omitting them makes every real row look ineligible, so the
        # consolidation table stays empty.
        columns = (
            "episode_id, user_id, concept_id, task_type, query_excerpt, approach, "
            "observed_outcome, feedback_outcome, feedback_status, created_at, "
            "attempt_id, feedback_attempt_id"
        )
        if self.episode_evidence_enabled():
            columns += ", assessment_status, evidence_source, student_response"
        try:
            res = (
                self.client.table("episodic_memory")
                .select(columns)
                .eq("user_id", user_id)
                .eq("status", "active")
                .is_("consolidated_at", "null")
                .order("created_at", desc=True)
                .limit(80)
                .execute()
            )
            return [
                row for row in (res.data or [])
                if isinstance(row, dict) and str(row.get("user_id")) == str(user_id)
            ]
        except Exception:
            return []

    def list_users_with_open_episodes(self) -> List[str]:
        """Distinct user ids that still have an unconsolidated active episode."""
        if not self.consolidation_schema_ready():
            return []
        try:
            res = (
                self.client.table("episodic_memory")
                .select("user_id")
                .eq("status", "active")
                .is_("consolidated_at", "null")
                .order("created_at", desc=False)
                .limit(500)
                .execute()
            )
        except Exception:
            return []
        seen: List[str] = []
        for row in res.data or []:
            user_id = str((row or {}).get("user_id") or "")
            if user_id and user_id not in seen:
                seen.append(user_id)
        return seen

    def active_learner_facts(self, user_id: str) -> List[Dict[str, Any]]:
        """Active facts for duplicate checks. No embeddings, this user only."""
        if not user_id or not self.memory_enabled():
            return []
        try:
            res = (
                self.client.table("learner_memory")
                .select("memory_id, user_id, kind, fact, subject_key")
                .eq("user_id", user_id)
                .is_("superseded_by", "null")
                .limit(200)
                .execute()
            )
            return [
                row for row in (res.data or [])
                if isinstance(row, dict) and str(row.get("user_id") or user_id) == str(user_id)
            ]
        except Exception:
            return []

    def consolidation_seen(self, user_id: str, cluster_key: str) -> bool:
        """True when this exact episode set was already proposed for this user."""
        if not user_id or not cluster_key or not self.consolidation_schema_ready():
            return False
        try:
            res = (
                self.client.table("episodic_consolidation")
                .select("cluster_key")
                .eq("user_id", user_id)
                .eq("cluster_key", cluster_key)
                .limit(1)
                .execute()
            )
            return bool(res.data)
        except Exception:
            return False

    def record_consolidation(
        self,
        *,
        user_id: str,
        cluster_key: str,
        episode_ids: List[str],
        result: str,
        memory_id: Optional[str] = None,
        fact: Optional[str] = None,
    ) -> bool:
        """Remember one proposal so the same cluster is not sent to the model again.

        A later saved or duplicate result updates the claim row. A second insert
        would hit the (user_id, cluster_key) primary key and be discarded, leaving
        the table stuck on claimed or empty.
        """
        if not user_id or not cluster_key or not self.consolidation_schema_ready():
            return False
        payload = {
            "user_id": user_id,
            "cluster_key": cluster_key,
            "episode_ids": list(episode_ids),
            "result": result,
            "memory_id": memory_id,
            "fact": fact,
        }
        try:
            existing = (
                self.client.table("episodic_consolidation")
                .select("result")
                .eq("user_id", user_id)
                .eq("cluster_key", cluster_key)
                .limit(1)
                .execute()
            )
            found = (existing.data or [None])[0]
            if found is not None:
                if result == "claimed":
                    return False
                (
                    self.client.table("episodic_consolidation")
                    .update(
                        {
                            "episode_ids": list(episode_ids),
                            "result": result,
                            "memory_id": memory_id,
                            "fact": fact,
                        }
                    )
                    .eq("user_id", user_id)
                    .eq("cluster_key", cluster_key)
                    .execute()
                )
                return True
            self.client.table("episodic_consolidation").insert(payload).execute()
            return True
        except Exception as exc:
            logging.getLogger("jee_tutor.db").warning(
                "episodic_consolidation write failed result=%s: %s",
                result,
                str(exc)[:200],
            )
            return False

    def mark_episodes_consolidated(
        self,
        user_id: str,
        episode_ids: List[str],
        memory_id: Optional[str],
    ) -> None:
        """Consume episodes that became a fact, or matched one that already exists.

        Updates are filtered by user_id. A missing row is a no-op.
        """
        if not user_id or not episode_ids or not self.consolidation_schema_ready():
            return None
        stamp = datetime.now(timezone.utc).isoformat()
        for episode_id in episode_ids:
            try:
                (
                    self.client.table("episodic_memory")
                    .update(
                        {
                            "consolidated_at": stamp,
                            "consolidation_memory_id": memory_id,
                            "updated_at": stamp,
                        }
                    )
                    .eq("user_id", user_id)
                    .eq("episode_id", episode_id)
                    .execute()
                )
            except Exception:
                continue
        return None

    # ─────────────────────────── registration helper ──────────────────────────

    def register_user(
        self,
        name: str,
        target_exam: str,
        preferred_depth: str = "standard",
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Full registration:  create users row + 33 user_concept_state rows.
        Returns the created user row.
        """
        user = self.create_user(name, target_exam, preferred_depth, user_id)
        concepts = self.read_concepts()
        if concepts:
            self.batch_create_user_concept_states(user["user_id"], concepts)
        return user


# ── Singleton ─────────────────────────────────────────────────────────────────
db = SupabaseClient(
    config.SUPABASE_URL,
    config.SUPABASE_KEY,
    getattr(config, "RETRIEVAL_SUPABASE_URL", None),
    getattr(config, "RETRIEVAL_SUPABASE_KEY", None),
)
