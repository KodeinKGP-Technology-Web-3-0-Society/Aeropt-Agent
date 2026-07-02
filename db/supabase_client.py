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
            .select("turn_id, role, content, turn_index, is_summarized, agent_used")
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

    def insert_chat_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        turn_index: int,
        agent_used: Optional[str] = None,
        concepts_referenced: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "turn_index": turn_index,
            "is_summarized": False,
        }
        if agent_used:
            row["agent_used"] = agent_used
        if concepts_referenced:
            row["concepts_referenced"] = concepts_referenced
        res = self.client.table("chat_turns").insert(row).execute()
        return res.data[0]

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
    ) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "user_id": user_id,
            "session_id": session_id,
            "concept_id": concept_id,
            "is_correct": is_correct,
        }
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
