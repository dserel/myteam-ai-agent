"""
storage.py
----------
Thin wrapper για το Supabase Postgres. Φάση Β: query_history + feedback.

Χρειάζεται:
    SUPABASE_URL          π.χ. https://xxxx.supabase.co
    SUPABASE_SERVICE_KEY  service_role key (όχι anon — γράφουμε από backend)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from supabase import Client, create_client

log = logging.getLogger("storage")


@dataclass
class StorageClient:
    url: str
    service_key: str

    def __post_init__(self) -> None:
        self._client: Client = create_client(self.url, self.service_key)

    # ---------- query_history ----------------------------------------------

    def log_query(
        self,
        *,
        username: str,
        tenant_club_id: int,
        user_role: str,
        tenant_user_id: Optional[int],
        question: str,
        generated_sql: Optional[str] = None,
        validated_sql: Optional[str] = None,
        row_count: Optional[int] = None,
        answer: Optional[str] = None,
        rejected_reason: Optional[str] = None,
        error: Optional[str] = None,
        elapsed_ms: Optional[int] = None,
    ) -> str:
        """Returns id of inserted row."""
        payload = {
            "username": username,
            "tenant_club_id": tenant_club_id,
            "user_role": user_role,
            "tenant_user_id": tenant_user_id,
            "question": question,
            "generated_sql": generated_sql,
            "validated_sql": validated_sql,
            "row_count": row_count,
            "answer": answer,
            "rejected_reason": rejected_reason,
            "error": error,
            "elapsed_ms": elapsed_ms,
        }
        resp = self._client.table("query_history").insert(payload).execute()
        return resp.data[0]["id"]

    def set_feedback(
        self,
        history_id: str,
        *,
        feedback: int,                 # -1, 0, 1
        feedback_note: Optional[str] = None,
        corrected_sql: Optional[str] = None,
    ) -> None:
        payload: dict[str, Any] = {"feedback": feedback}
        if feedback_note is not None:
            payload["feedback_note"] = feedback_note
        if corrected_sql is not None:
            payload["corrected_sql"] = corrected_sql
        self._client.table("query_history").update(payload).eq("id", history_id).execute()

    def mark_golden(self, history_id: str, *, golden: bool = True) -> None:
        self._client.table("query_history").update({"is_golden": golden}).eq("id", history_id).execute()

    def list_history(
        self,
        *,
        limit: int = 100,
        only_feedback: Optional[int] = None,
        only_golden: bool = False,
    ) -> list[dict]:
        q = self._client.table("query_history").select("*").order("created_at", desc=True).limit(limit)
        if only_feedback is not None:
            q = q.eq("feedback", only_feedback)
        if only_golden:
            q = q.eq("is_golden", True)
        resp = q.execute()
        return resp.data or []

    # ---------- golden_examples --------------------------------------------

    def add_golden_example(
        self,
        *,
        user_role: str,
        question: str,
        sql_template: str,
        explanation: Optional[str] = None,
        tags: Optional[list[str]] = None,
        source_history_id: Optional[str] = None,
    ) -> str:
        payload = {
            "user_role": user_role,
            "question": question,
            "sql_template": sql_template,
            "explanation": explanation,
            "tags": tags,
            "source_history_id": source_history_id,
        }
        resp = self._client.table("golden_examples").insert(payload).execute()
        return resp.data[0]["id"]

    def list_golden_examples(self, *, user_role: Optional[str] = None) -> list[dict]:
        q = self._client.table("golden_examples").select("*").eq("enabled", True)
        if user_role:
            q = q.in_("user_role", [user_role, "any"])
        return q.execute().data or []

    # ---------- schema_annotations -----------------------------------------

    def list_schema_annotations(self) -> list[dict]:
        return (
            self._client.table("schema_annotations")
            .select("*")
            .eq("enabled", True)
            .execute()
            .data
            or []
        )

    def add_schema_annotation(
        self,
        *,
        table_name: str,
        column_name: Optional[str],
        note: str,
        severity: str = "info",
        created_by: Optional[str] = None,
    ) -> str:
        payload = {
            "table_name": table_name,
            "column_name": column_name,
            "note": note,
            "severity": severity,
            "created_by": created_by,
        }
        resp = self._client.table("schema_annotations").insert(payload).execute()
        return resp.data[0]["id"]

    # ---------- health -----------------------------------------------------

    def test_connection(self) -> tuple[bool, str]:
        try:
            self._client.table("query_history").select("id").limit(1).execute()
            return True, "OK — Supabase reachable & query_history exists"
        except Exception as e:
            return False, f"Supabase error: {e}"
