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

    def write_answer(self, question: str, sql: str, rows: list[dict]) -> str:
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
            ),
        )
        return (resp.text or "").strip()


# ---------- Legacy module-level (env-driven) -------------------------------

def _from_env() -> LLMClient:
    return LLMClient(api_key=os.environ["GEMINI_API_KEY"])


def generate_sql(question: str, context: dict) -> dict:
    return _from_env().generate_sql(question, context)


def write_answer(question: str, sql: str, rows: list[dict]) -> str:
    return _from_env().write_answer(question, sql, rows)
