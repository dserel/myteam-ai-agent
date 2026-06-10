"""
llm.py
------
Wrappers γύρω από Anthropic Claude.

Δύο τρόποι χρήσης:
  1. As class (preferred):
        client = LLMClient(api_key="sk-ant-...", model="claude-sonnet-4-6")
        out = client.generate_sql(question, context)
        ans = client.write_answer(question, sql, rows)   # -> {"text","chart"}
  2. Module-level (legacy, χρησιμοποιεί ANTHROPIC_API_KEY env var)

Σημείωση: Το Claude δεν έχει `response_mime_type=application/json`.
Εξαναγκάζουμε JSON output μέσω instruction στο system prompt και
κάνουμε robust parse με `_extract_json` (αφαιρεί τυχόν ```json fences).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anthropic import Anthropic

BASE_DIR = Path(__file__).resolve().parent.parent
SCHEMA_PATH = BASE_DIR / "schema_pruned.md"
SQL_PROMPT_PATH = BASE_DIR / "prompts" / "sql_generator.md"
ANSWER_PROMPT_PATH = BASE_DIR / "prompts" / "answer_writer.md"

# Default: Sonnet για καλύτερη ποιότητα από Gemini Flash.
# Για φθηνότερο/γρηγορότερο: "claude-haiku-4-5-20251001".
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

_JSON_ONLY_SUFFIX = (
    "\n\nIMPORTANT: Απάντησε ΜΟΝΟ με ένα έγκυρο JSON object. "
    "Χωρίς markdown code fences, χωρίς κείμενο πριν ή μετά το JSON."
)


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
        self._client = Anthropic(api_key=self.api_key)

    # ---------- low-level helper -------------------------------------------

    def _complete_json(self, system: str, user_msg: str, temperature: float) -> dict:
        """
        Στέλνει ένα single-turn message και ζητάει JSON object output
        μέσω instruction. Robust parse με `_extract_json`.
        """
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            temperature=temperature,
            system=system + _JSON_ONLY_SUFFIX,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text if resp.content else ""
        return _extract_json(raw)

    # ---------- health -----------------------------------------------------

    def test_connection(self) -> tuple[bool, str]:
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=5,
                temperature=0.0,
                messages=[{"role": "user", "content": "ping"}],
            )
            txt = resp.content[0].text if resp.content else ""
            return True, f"OK — Claude ({self.model}) απάντησε: {txt[:50]!r}"
        except Exception as e:
            return False, f"Claude error: {e}"

    # ---------- SQL generation ---------------------------------------------

    def generate_sql(self, question: str, context: dict) -> dict:
        system = _render_sql_prompt(context)
        return self._complete_json(system, question, self.sql_temperature)

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
        return self._complete_json(system, user_msg, self.sql_temperature)

    # ---------- answer writing ---------------------------------------------

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
        try:
            out = self._complete_json(system, user_msg, self.answer_temperature)
        except Exception:
            # Fallback: αν δεν επιστρέψει valid JSON, ξανατρέχουμε χωρίς prefill
            # και επιστρέφουμε ό,τι κείμενο πάρουμε σαν text.
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                temperature=self.answer_temperature,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = (resp.content[0].text if resp.content else "").strip()
            try:
                out = _extract_json(raw)
            except Exception:
                return {"text": raw, "chart": None}
        # Defensive: σιγουρέψου ότι έχει τα δύο keys
        return {"text": (out.get("text") or "").strip(), "chart": out.get("chart")}


# ---------- Legacy module-level (env-driven) -------------------------------

def _from_env() -> LLMClient:
    return LLMClient(api_key=os.environ["ANTHROPIC_API_KEY"])


def generate_sql(question: str, context: dict) -> dict:
    return _from_env().generate_sql(question, context)


def write_answer(question: str, sql: str, rows: list[dict]) -> dict:
    return _from_env().write_answer(question, sql, rows)
