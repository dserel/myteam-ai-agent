"""
metabase.py
-----------
Λεπτό wrapper γύρω από το Metabase /api/dataset endpoint για να τρέχουμε
ad-hoc SQL χωρίς να φτιάχνουμε saved card κάθε φορά.

Δύο τρόποι χρήσης:
  1. As config class (preferred — δουλεύει με Streamlit runtime config):
        client = MetabaseClient(url, session_token, database_id)
        rows = client.run_sql("SELECT ...")
  2. As module-level (legacy — env vars):
        from src.metabase import run_sql
        rows = run_sql("SELECT ...")
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests


class MetabaseError(Exception):
    pass


@dataclass
class MetabaseClient:
    """
    Μπορείς να δώσεις είτε `api_key` (preferred — δεν λήγει) είτε
    `session_token` (λήγει ~14 μέρες). Αρκεί το ένα από τα δύο.
    """
    url: str
    database_id: int
    api_key: str = ""
    session_token: str = ""
    timeout_s: int = 40

    def __post_init__(self) -> None:
        self.url = self.url.rstrip("/")
        if not (self.api_key or self.session_token):
            raise ValueError("MetabaseClient: provide either api_key or session_token")

    @property
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-KEY"] = self.api_key
        else:
            h["X-Metabase-Session"] = self.session_token
        return h

    def test_connection(self) -> tuple[bool, str]:
        """Γρήγορος έλεγχος ότι τα credentials είναι σωστά.
        Χρησιμοποιεί /api/database που δουλεύει και με API key και με session."""
        try:
            r = requests.get(f"{self.url}/api/database", headers=self._headers, timeout=10)
            if r.status_code == 200:
                body = r.json()
                # /api/database returns {"data":[...]} σε νέες εκδόσεις, ή list σε παλιές
                dbs = body.get("data", body) if isinstance(body, dict) else body
                if isinstance(dbs, list):
                    auth = "API key" if self.api_key else "session"
                    return True, f"OK — auth={auth}, found {len(dbs)} database(s)"
                return True, "OK (response shape unexpected, but auth ok)"
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return False, f"connection error: {e}"

    def run_sql(self, sql: str) -> list[dict[str, Any]]:
        payload = {
            "type": "native",
            "native": {"query": sql},
            "database": self.database_id,
        }
        url = f"{self.url}/api/dataset"
        try:
            r = requests.post(url, headers=self._headers, json=payload, timeout=self.timeout_s)
        except requests.exceptions.RequestException as e:
            # timeout / connection / DNS — όλα γίνονται MetabaseError ώστε ο caller
            # (chat agent) να τα πιάνει ομοιόμορφα και να μην κρασάρει το app.
            raise MetabaseError(f"request error: {type(e).__name__}: {e}") from e

        if r.status_code not in (200, 202):
            raise MetabaseError(f"HTTP {r.status_code}: {r.text[:500]}")

        try:
            body = r.json()
        except ValueError as e:
            raise MetabaseError(f"invalid JSON response: {r.text[:300]}") from e
        if not isinstance(body, dict):
            raise MetabaseError(f"unexpected response shape: {str(body)[:300]}")
        if body.get("status") == "failed":
            raise MetabaseError(f"query failed: {body.get('error', 'unknown')}")

        data = body.get("data", {}) or {}
        cols = [c["name"] for c in data.get("cols", [])]
        rows = data.get("rows", [])
        return [dict(zip(cols, row)) for row in rows]


# ---------- Legacy module-level (env-driven) -------------------------------

def _from_env() -> MetabaseClient:
    return MetabaseClient(
        url=os.environ["METABASE_URL"],
        api_key=os.environ.get("METABASE_API_KEY", ""),
        session_token=os.environ.get("METABASE_SESSION", ""),
        database_id=int(os.environ["METABASE_DATABASE_ID"]),
    )


def run_sql(sql: str, *, timeout_s: int = 15) -> list[dict[str, Any]]:
    """Backwards-compat: env-driven one-shot."""
    client = _from_env()
    client.timeout_s = timeout_s
    return client.run_sql(sql)
