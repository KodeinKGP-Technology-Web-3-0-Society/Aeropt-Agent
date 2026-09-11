"""
All Supabase read/write operations for the JEE Tutor Agent.

Single SupabaseClient instance is created once in this module and imported
by every agent that needs DB access.  No agent ever calls supabase-py directly.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from supabase import create_client, Client

import config


class SupabaseClient:
    def __init__(self, url: str, key: str) -> None:
        self.client: Client = create_client(url, key)
        # None = not probed yet; set by memory_enabled() on first use.
        self._memory_enabled: Optional[bool] = None
        # Set to True the first time chat_turns rejects a post-007 agent label.
        self._agent_used_is_narrow: bool = False

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
            .single()
            .execute()
        )
        return res.data

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

    def list_user_sessions(self, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        res = (
            self.client.table("sessions")
            .select("session_id, user_id, interaction_count, session_summary, updated_at")
            .eq("user_id", user_id)
            .order("updated_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []

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

    def create_session(self, user_id: str) -> Dict[str, Any]:
        row = {
            "user_id": user_id,
            "interaction_count": 0,
            "active_quiz": False,
        }
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
            self.client.table("concepts")
            .select("concept_id, concept_name, chapter, subject")
            .execute()
        )
        return res.data or []

    # ─────────────────────────── content ──────────────────────────────────────

    def read_content_by_ids(self, content_ids: List[str]) -> List[Dict[str, Any]]:
        if not content_ids:
            return []
        res = (
            self.client.table("content")
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
        student_answer: Optional[str],
        root_cause_concept_id: Optional[str] = None,
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
        return res.data[0]

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

    def read_learner_memories(
        self,
        user_id: str,
        *,
        subject_key: Optional[str] = None,
        kinds: Optional[List[str]] = None,
        limit: int = 12,
        min_confidence: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """Active learner memories, strongest first. Empty list if memory is off."""
        if not self.memory_enabled():
            return []
        try:
            q = (
                self.client.table("learner_memory")
                .select("memory_id, kind, subject_key, fact, evidence, confidence, created_at")
                .eq("user_id", user_id)
                .is_("superseded_by", "null")
                .gte("confidence", min_confidence)
            )
            if subject_key:
                q = q.eq("subject_key", subject_key)
            if kinds:
                q = q.in_("kind", kinds)
            res = q.order("confidence", desc=True).limit(limit).execute()
            return res.data or []
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
db = SupabaseClient(config.SUPABASE_URL, config.SUPABASE_KEY)
