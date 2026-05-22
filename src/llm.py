"""
llm.py
------
Wrappers γύρω από Gemini Flash.

Δύο τρόποι χρήσης:
  1. As class (preferred):
        client = LLMClient(api_key="...", model="gemini-2.5-flash")
        out = client.generate_sql(question, context)
        text = client.write_answer(question, sql, rows)
  2. Module-level (legacy, χρησιμοποιεί GEMINI_API_KEY env var)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

BASE_DIR = Path(__file__).resolve().parent.parent
SCHEMA_PATH = BASE_DIR / "schema_pruned.md"
SQL_PROMPT_PATH = BASE_DIR / "prompts" / "sql_generator.md"
ANSWER_PROMPT_PATH = BASE_DIR / "prompts" / "answer_writer.md"

DEFAULT_MODEL = "gemini-2.5-flash"


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK.search(text)
        if not m:
            raise ValueError(f"could not parse JSON from LLM output:\n{text[:500]}")
        return json.loads(m.group(0))


def _render_sql_prompt(context: dict) -> str:
    template = SQL_PROMPT_PATH.read_text(encoding="utf-8")
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    return (
        template
        .replace("{{tenant_club_id}}", str(context["tenant_club_id"]))
        .replace("{{tenant_user_id}}", str(context.get("tenant_user_id") or "null"))
        .replace("{{user_role}}", context["user_role"])
        .replace("{{today}}", context["today"])
        .replace("{{schema_pruned_md_inline}}", schema)
    )


@dataclass
class LLMClient:
    api_key: str
    model: str = DEFAULT_MODEL
    sql_temperature: float = 0.1
    answer_temperature: float = 0.3

    def __post_init__(self) -> None:
        self._client = genai.Client(api_key=self.api_key)

    def test_connection(self) -> tuple[bool, str]:
        try:
            resp = self._client.models.generate_content(
                model=self.model,
                contents=[types.Content(role="user", parts=[types.Part(text="ping")])],
                config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=5),
            )
            return True, f"OK — got: {(resp.text or '')[:50]!r}"
        except Exception as e:
            return False, f"Gemini error: {e}"

    def generate_sql(self, question: str, context: dict) -> dict:
        system = _render_sql_prompt(context)
        resp = self._client.models.generate_content(
            model=self.model,
            contents=[types.Content(role="user", parts=[types.Part(text=question)])],
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=self.sql_temperature,
                response_mime_type="application/json",
            ),
        )
        return _extract_json(resp.text or "{}")

    def fix_sql_after_error(
        self,
        question: str,
        failed_sql: str,
        error: str,
        context: dict,
        previous_attempts: list[str] | None = None,
    ) -> dict:
        """
        Στέλνει στο LLM το failed SQL + το error message και ζητάει
        διόρθωση. Χρησιμοποιεί το ίδιο system prompt (sql_generator.md)
        αλλά το user message περιέχει το context της αποτυχίας.
        """
        system = _render_sql_prompt(context)
        attempts_block = ""
        if previous_attempts:
            attempts_block = (
                "\n\nΠΡΟΗΓΟΥΜΕΝΕΣ ΑΠΟΠΕΙΡΕΣ ΠΟΥ ΑΠΕΤΥΧΑΝ (μην τις επαναλάβεις):\n"
                + "\n---\n".join(previous_attempts)
            )

        user_msg = (
            f"Η αρχική ερώτηση του χρήστη ήταν:\n\n"
            f"  «{question}»\n\n"
            f"Παρήγαγες το παρακάτω SQL:\n\n```sql\n{failed_sql}\n```\n\n"
            f"Όμως η εκτέλεση απέτυχε με αυτό το error:\n\n"
            f"  {error}\n\n"
            f"Διόρθωσέ το. Αν το error δείχνει ότι κάποια στήλη/πίνακας δεν υπάρχει, "
            f"ξανασκέψου τη δομή — μπορεί να έκανες υπόθεση που δεν ισχύει. "
            f"Πιθανή στρατηγική: χρησιμοποίησε εναλλακτικές στήλες (π.χ. `type` αντί για `name` "
            f"για lookup tables), ή κάνε JOIN με διαφορετικό τρόπο. "
            f"Επέστρεψε ΜΟΝΟ JSON με νέο διορθωμένο SQL."
            f"{attempts_block}"
        )
        resp = self._client.models.generate_content(
            model=self.model,
            contents=[types.Content(role="user", parts=[types.Part(text=user_msg)])],
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=self.sql_temperature,
                response_mime_type="application/json",
            ),
        )
        return _extract_json(resp.text or "{}")

    def write_answer(self, question: str, sql: str, rows: list[dict]) -> dict:
        """
        Returns dict: {"text": str, "chart": dict|None}
        Όπου chart spec έχει: {type, title, x, y, color, agg, value_format}
        """
        system = ANSWER_PROMPT_PATH.read_text(encoding="utf-8")
        user_msg = (
            f"**Question**: {question}\n\n"
            f"**SQL**:\n```sql\n{sql}\n```\n\n"
            f"**Rows ({len(rows)} total)**:\n```json\n"
            f"{json.dumps(rows[:50], ensure_ascii=False, default=str)}\n```\n"
        )
        resp = self._client.models.generate_content(
            model=self.model,
            contents=[types.Content(role="user", parts=[types.Part(text=user_msg)])],
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=self.answer_temperature,
                response_mime_type="application/json",
            ),
        )
        raw = (resp.text or "").strip()
        try:
            out = _extract_json(raw)
        except Exception:
            # Fallback: αν δεν επιστρέψει valid JSON, treat όλο σαν text
            return {"text": raw, "chart": None}
        # Defensive: σιγουρέψου ότι έχει τα δύο keys
        return {"text": out.get("text", "").strip(), "chart": out.get("chart")}


# ---------- Legacy module-level (env-driven) -------------------------------

def _from_env() -> LLMClient:
    return LLMClient(api_key=os.environ["GEMINI_API_KEY"])


def generate_sql(question: str, context: dict) -> dict:
    return _from_env().generate_sql(question, context)


def write_answer(question: str, sql: str, rows: list[dict]) -> str:
    return _from_env().write_answer(question, sql, rows)
