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
from typing import Any, Iterator

from anthropic import Anthropic

BASE_DIR = Path(__file__).resolve().parent.parent
SCHEMA_PATH = BASE_DIR / "schema_pruned.md"
SQL_PROMPT_PATH = BASE_DIR / "prompts" / "sql_generator.md"
ANSWER_PROMPT_PATH = BASE_DIR / "prompts" / "answer_writer.md"

# Default: Haiku — υψηλό rate limit, φθηνό/γρήγορο, αρκετό για text-to-SQL.
# Για καλύτερη ποιότητα (αν το tier το επιτρέπει): "claude-sonnet-4-6".
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 4096

_JSON_ONLY_SUFFIX = (
    "\n\nIMPORTANT: Απάντησε ΜΟΝΟ με ένα έγκυρο JSON object. "
    "Χωρίς markdown code fences, χωρίς κείμενο πριν ή μετά το JSON."
)

_REWRITE_SYSTEM = (
    "Είσαι βοηθός που ξαναγράφει ερωτήσεις. Σου δίνεται το ιστορικό μιας "
    "συνομιλίας (ερωτήσεις χρήστη για δεδομένα ενός αθλητικού συλλόγου) και "
    "μια νέα ερώτηση. Αν η νέα ερώτηση είναι follow-up που εξαρτάται από τα "
    "προηγούμενα (π.χ. «και πέρσι;», «μόνο τους ενεργούς», «αυτής της ομάδας»), "
    "ξαναγράψ' την ώστε να είναι ΠΛΗΡΩΣ ΑΥΤΟΤΕΛΗΣ και κατανοητή χωρίς το ιστορικό. "
    "Αν είναι ήδη αυτοτελής, επίστρεψέ την ΑΥΤΟΥΣΙΑ. "
    "Διατήρησε τη γλώσσα (ελληνικά) και το νόημα. "
    "Απάντησε ΜΟΝΟ με το κείμενο της ερώτησης — χωρίς εισαγωγικά, χωρίς εξήγηση, χωρίς πρόθεμα."
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


def _partial_text_from_json(acc: str) -> str:
    """
    Εξάγει προοδευτικά την τιμή του πεδίου "text" από ένα JSON που γράφεται
    ακόμη (streaming). Επιστρέφει ό,τι κείμενο έχει φτάσει μέχρι στιγμής,
    κάνοντας unescape τα \n / \t / \". Tolerant — ποτέ δεν πετάει.
    """
    i = acc.find('"text"')
    if i < 0:
        return ""
    j = acc.find(":", i)
    if j < 0:
        return ""
    k = acc.find('"', j + 1)
    if k < 0:
        return ""
    out: list[str] = []
    esc = False
    idx = k + 1
    while idx < len(acc):
        c = acc[idx]
        if esc:
            out.append({"n": "\n", "t": "\t", "r": "\r"}.get(c, c))
            esc = False
        elif c == "\\":
            esc = True
        elif c == '"':
            break
        else:
            out.append(c)
        idx += 1
    return "".join(out)


def _is_rate_limit(e: Exception) -> bool:
    if e.__class__.__name__ in ("RateLimitError",):
        return True
    msg = str(e)
    return "rate_limit" in msg or "429" in msg


_EVENT_TIPS_SYSTEM = (
    "Είσαι πρακτικός βοηθός γονέα αθλητή. Λαμβάνεις λίστα events (JSON) με ώρα, τίτλο, "
    "τοποθεσία και (προαιρετικά) καιρό. Για ΚΑΘΕ event γράψε ΕΝΑ σύντομο heads-up στα ελληνικά "
    "(<=160 χαρακτήρες):\n"
    "- Πρακτική πρόταση «φύγετε ~X′ νωρίτερα» αν η ώρα/τοποθεσία υποδηλώνει κίνηση/δύσκολο παρκάρισμα "
    "(π.χ. κεντρικές περιοχές, Σαββατοκύριακο πρωί). Πες ότι είναι ΕΚΤΙΜΗΣΗ.\n"
    "- Τι να φέρει (νερό, κατάλληλα παπούτσια· για αγώνα: εμφάνιση).\n"
    "- Αν δίνεται καιρός: ζέστη→νερό/καπέλο, βροχή→ομπρέλα/πιθανή αλλαγή.\n"
    "Επίστρεψε ΜΟΝΟ JSON: {\"tips\": [\"...\", ...]} με ΑΚΡΙΒΩΣ ίδιο πλήθος και σειρά με τα events. "
    "Χωρίς preamble."
)


_DRAFT_SYSTEM = (
    "Γράφεις εκ μέρους αθλητικού συλλόγου ένα ευγενικό, ζεστό αλλά επαγγελματικό μήνυμα "
    "προς τον γονέα ενός αθλητή. Στα ελληνικά.\n"
    "- Αν κανάλι = email: ξεκίνα με «Θέμα: ...» σε πρώτη γραμμή, μετά κενή γραμμή και το σώμα.\n"
    "- Αν κανάλι = sms: 1-2 σύντομες προτάσεις, έως ~300 χαρακτήρες, χωρίς θέμα.\n"
    "- Τόνος: φιλικός, υποστηρικτικός — ΟΧΙ απειλητικός ή πιεστικός.\n"
    "- ΜΗΝ υπόσχεσαι εκπτώσεις, τιμές ή όρους. ΜΗΝ εφευρίσκεις στοιχεία.\n"
    "- Αν δεν ξέρεις όνομα γονέα, χρησιμοποίησε ουδέτερη προσφώνηση (π.χ. «Αγαπητοί γονείς»).\n"
    "- Επίστρεψε ΜΟΝΟ το μήνυμα, χωρίς εξηγήσεις πριν/μετά."
)


_BRIEFING_SYSTEM = (
    "Είσαι έμπειρος σύμβουλος αθλητικών συλλόγων. Λαμβάνεις μετρικές ενός συλλόγου σε JSON "
    "και γράφεις ΣΥΝΤΟΜΟ executive briefing για τον manager, στα ελληνικά, σε markdown.\n"
    "Κανόνες:\n"
    "- Ξεκίνα με ΜΙΑ πρόταση συνολικής εικόνας.\n"
    "- Μετά 3-4 σημεία προτεραιότητας, ταξινομημένα κατά σημαντικότητα / οικονομική επίπτωση. "
    "Κάθε σημείο: τι συμβαίνει (με ΣΥΓΚΕΚΡΙΜΕΝΑ νούμερα) + **Ενέργεια:** συγκεκριμένη πρόταση δράσης.\n"
    "- Μορφή ποσών: €1.234. Ποσοστά: 87,8%.\n"
    "- Ανέδειξε ρίσκα (χαμηλή παρουσία, ληξιπρόθεσμες/ανεξόφλητες, αθλητές σε κίνδυνο) ΚΑΙ θετικά "
    "(αύξηση εσόδων/εγγραφών, υψηλή είσπραξη).\n"
    "- Μέγιστο ~180 λέξεις. ΟΧΙ πίνακες, ΟΧΙ SQL, ΟΧΙ preamble τύπου «Με βάση τα δεδομένα».\n"
    "- Αν μια μετρική είναι null/λείπει, αγνόησέ την — μην την αναφέρεις.\n"
    "- ΧΡΗΣΗ ΕΦΑΡΜΟΓΗΣ (πεδίο «χρήση_features»): αν κάποιο feature είναι 'δεν χρησιμοποιείται' "
    "ή 'σταμάτησε', πρότεινε ΕΝΕΡΓΑ στον manager να (ξ)αρχίσει να το χρησιμοποιεί, εξηγώντας το "
    "συγκεκριμένο όφελος (π.χ. καταγραφή εξόδων → πραγματική εικόνα κερδοφορίας & ταμειακή ροή· "
    "παρουσίες → έγκαιρος εντοπισμός αθλητών σε κίνδυνο· αποτελέσματα αγώνων → ιστορικό/στατιστικά). "
    "Ανέφερε από πότε σταμάτησε, αν δίνεται."
)


_RATE_LIMIT_MSG = (
    "Υπέρβαση ορίου ρυθμού του Anthropic API (rate limit). "
    "Περίμενε ~1 λεπτό και ξαναδοκίμασε. Για μόνιμη λύση: το μοντέλο είναι ήδη Haiku "
    "(υψηλό όριο) — αν θες Sonnet, αναβάθμισε το usage tier στο console.anthropic.com."
)


def _cached(text: str) -> dict:
    """System block με ephemeral prompt caching (μένει σταθερό -> cache hit)."""
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def _render_sql_prompt(context: dict) -> tuple[str, str]:
    """
    Returns (static_prompt, dynamic_block).
      static  = κανόνες + schema + few-shot — ΣΤΑΘΕΡΟ σε κάθε κλήση => cacheable.
      dynamic = τρέχον context + golden examples + schema annotations — αλλάζει.
    Το split επιτρέπει prompt caching του (μεγάλου) static κομματιού.
    """
    template = SQL_PROMPT_PATH.read_text(encoding="utf-8")
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    static = template.replace("{{schema_pruned_md_inline}}", schema)
    golden = (context.get("golden_examples_inline") or "").strip() or "(καμία ακόμη)"
    annotations = (context.get("schema_annotations_inline") or "").strip() or "(καμία)"
    dynamic = (
        "## Τρέχον context\n"
        f"tenant_club_id = {context['tenant_club_id']}\n"
        f"tenant_user_id = {context.get('tenant_user_id') or 'null'}\n"
        f"user_role      = {context['user_role']}\n"
        f"today          = {context['today']}\n\n"
        "## ⭐ Golden examples (επικυρωμένα από admin — προτίμησέ τα ως πρότυπα όταν ταιριάζουν)\n"
        f"{golden}\n\n"
        "## 📝 Σημειώσεις schema (διορθώσεις/διευκρινίσεις — ΥΠΕΡΙΣΧΥΟΥΝ του schema)\n"
        f"{annotations}\n"
    )
    return static, dynamic


@dataclass
class LLMClient:
    api_key: str
    model: str = DEFAULT_MODEL
    sql_temperature: float = 0.1
    answer_temperature: float = 0.3

    def __post_init__(self) -> None:
        # max_retries: ο SDK κάνει auto-retry στο 429 (τηρώντας το retry-after)
        self._client = Anthropic(api_key=self.api_key, max_retries=3)

    # ---------- low-level helper -------------------------------------------

    def _complete_json(self, system_blocks: list, user_msg: str, temperature: float) -> dict:
        """
        Στέλνει ένα single-turn message και ζητάει JSON object output μέσω instruction.
        `system_blocks` = λίστα content blocks (το μεγάλο static μπλοκ έχει cache_control).
        """
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            temperature=temperature,
            system=system_blocks + [{"type": "text", "text": _JSON_ONLY_SUFFIX}],
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
        static, dynamic = _render_sql_prompt(context)
        blocks = [_cached(static), {"type": "text", "text": dynamic}]
        try:
            return self._complete_json(blocks, question, self.sql_temperature)
        except Exception as e:
            if _is_rate_limit(e):
                return {"error": _RATE_LIMIT_MSG}
            raise

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
        static, dynamic = _render_sql_prompt(context)
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
        blocks = [_cached(static), {"type": "text", "text": dynamic}]
        try:
            return self._complete_json(blocks, user_msg, self.sql_temperature)
        except Exception as e:
            if _is_rate_limit(e):
                return {"error": _RATE_LIMIT_MSG}
            raise

    # ---------- conversational memory -------------------------------------

    def rewrite_followup(self, question: str, history: list[dict] | None) -> str:
        """
        Ξαναγράφει ένα (πιθανώς ελλειπτικό) follow-up σε αυτόνομη ερώτηση,
        χρησιμοποιώντας το ιστορικό της συνομιλίας. Αν δεν υπάρχει ιστορικό
        ή η ερώτηση είναι ήδη αυτοτελής, επιστρέφει την ίδια την ερώτηση.
        Σε σφάλμα -> επιστρέφει το αρχικό question (graceful degradation).
        """
        if not history:
            return question
        convo: list[str] = []
        for m in history[-8:]:
            role = "Χρήστης" if m.get("role") == "user" else "Βοηθός"
            content = (m.get("content") or "").strip()
            if content:
                convo.append(f"{role}: {content[:500]}")
        if not convo:
            return question
        user_msg = (
            "ΙΣΤΟΡΙΚΟ ΣΥΝΟΜΙΛΙΑΣ:\n"
            + "\n".join(convo)
            + f"\n\nΝΕΑ ΕΡΩΤΗΣΗ ΧΡΗΣΤΗ: {question}"
        )
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=300,
                temperature=0.0,
                system=_REWRITE_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
            )
            out = (resp.content[0].text if resp.content else "").strip()
            return out or question
        except Exception:
            return question

    # ---------- answer writing ---------------------------------------------

    def write_answer(self, question: str, sql: str, rows: list[dict]) -> dict:
        """
        Returns dict: {"text": str, "chart": dict|None}
        Όπου chart spec έχει: {type, title, x, y, color, agg, value_format}
        """
        system_text = ANSWER_PROMPT_PATH.read_text(encoding="utf-8")
        blocks = [_cached(system_text)]
        user_msg = (
            f"**Question**: {question}\n\n"
            f"**SQL**:\n```sql\n{sql}\n```\n\n"
            f"**Rows ({len(rows)} total)**:\n```json\n"
            f"{json.dumps(rows[:50], ensure_ascii=False, default=str)}\n```\n"
        )
        try:
            out = self._complete_json(blocks, user_msg, self.answer_temperature)
        except Exception:
            # Fallback: ξανατρέχουμε και επιστρέφουμε ό,τι κείμενο πάρουμε σαν text.
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                temperature=self.answer_temperature,
                system=[_cached(system_text)],
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = (resp.content[0].text if resp.content else "").strip()
            try:
                out = _extract_json(raw)
            except Exception:
                return {"text": raw, "chart": None}
        # Defensive: σιγουρέψου ότι έχει τα keys
        return {
            "text": (out.get("text") or "").strip(),
            "chart": out.get("chart"),
            "followups": out.get("followups") or [],
        }


    def stream_answer(self, question: str, sql: str, rows: list[dict], out: dict) -> "Iterator[str]":
        """
        Streaming εκδοχή του write_answer. Κάνει yield το ΚΕΙΜΕΝΟ της απάντησης
        προοδευτικά (για ζωντανή εμφάνιση), και στο τέλος γεμίζει το `out` dict με
        {"text", "chart", "followups"}. Σε σφάλμα γεμίζει out["_error"] και σταματά —
        ο caller κάνει fallback στο write_answer.
        """
        system_text = ANSWER_PROMPT_PATH.read_text(encoding="utf-8")
        user_msg = (
            f"**Question**: {question}\n\n"
            f"**SQL**:\n```sql\n{sql}\n```\n\n"
            f"**Rows ({len(rows)} total)**:\n```json\n"
            f"{json.dumps(rows[:50], ensure_ascii=False, default=str)}\n```\n"
        )
        acc = ""
        try:
            with self._client.messages.stream(
                model=self.model,
                max_tokens=MAX_TOKENS,
                temperature=self.answer_temperature,
                system=[_cached(system_text), {"type": "text", "text": _JSON_ONLY_SUFFIX}],
                messages=[{"role": "user", "content": user_msg}],
            ) as stream:
                for delta in stream.text_stream:
                    acc += delta
                    yield _partial_text_from_json(acc)
        except Exception as e:
            out["_error"] = str(e)
            return
        try:
            parsed = _extract_json(acc)
        except Exception:
            parsed = {"text": _partial_text_from_json(acc) or acc, "chart": None}
        out["text"] = (parsed.get("text") or "").strip()
        out["chart"] = parsed.get("chart")
        out["followups"] = parsed.get("followups") or []


    def write_briefing(self, metrics: dict) -> str:
        """
        Παίρνει ένα dict με μετρικές συλλόγου και επιστρέφει markdown executive
        briefing (proactive insights + προτεινόμενες ενέργειες). Best-effort:
        σε rate limit / σφάλμα επιστρέφει σύντομο fallback μήνυμα.
        """
        user_msg = (
            "Μετρικές συλλόγου (JSON):\n```json\n"
            + json.dumps(metrics, ensure_ascii=False, default=str)
            + "\n```\nΓράψε το briefing."
        )
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=900,
                temperature=0.4,
                system=[_cached(_BRIEFING_SYSTEM)],
                messages=[{"role": "user", "content": user_msg}],
            )
            return (resp.content[0].text if resp.content else "").strip()
        except Exception as e:
            if _is_rate_limit(e):
                return "⚠️ " + _RATE_LIMIT_MSG
            return f"Δεν ήταν δυνατή η σύνταξη του briefing ({e})."


    def draft_parent_message(self, *, athlete: str, reason: str, details: str = "",
                             channel: str = "email", club_name: str = "ο σύλλογος") -> str:
        """Συντάσσει προσωποποιημένο μήνυμα (email/sms) προς τον γονέα. Best-effort."""
        user_msg = (
            f"Αθλητής/τρια: {athlete}\n"
            f"Σύλλογος: {club_name}\n"
            f"Λόγος επικοινωνίας: {reason}\n"
            f"Λεπτομέρειες: {details or '-'}\n"
            f"Κανάλι: {channel}"
        )
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=600,
                temperature=0.5,
                system=[_cached(_DRAFT_SYSTEM)],
                messages=[{"role": "user", "content": user_msg}],
            )
            return (resp.content[0].text if resp.content else "").strip()
        except Exception as e:
            if _is_rate_limit(e):
                return "⚠️ " + _RATE_LIMIT_MSG
            return f"Δεν ήταν δυνατή η σύνταξη του μηνύματος ({e})."


    def event_tips(self, events: list[dict]) -> list[str]:
        """Ένα σύντομο πρακτικό heads-up ανά event (ίδια σειρά). Best-effort -> [] σε σφάλμα."""
        if not events:
            return []
        slim = [
            {"start": str(e.get("start_date")), "title": e.get("title"),
             "location": e.get("location_alias"), "weather": e.get("weather")}
            for e in events
        ]
        user_msg = "Events:\n```json\n" + json.dumps(slim, ensure_ascii=False, default=str) + "\n```"
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=1200,
                temperature=0.4,
                system=[_cached(_EVENT_TIPS_SYSTEM)],
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = resp.content[0].text if resp.content else ""
            out = _extract_json(raw)
            tips = out.get("tips") or []
            # pad/truncate ώστε να ταιριάζει με τα events
            tips = (list(tips) + [""] * len(events))[:len(events)]
            return [str(t) for t in tips]
        except Exception:
            return [""] * len(events)


# ---------- Legacy module-level (env-driven) -------------------------------

def _from_env() -> LLMClient:
    return LLMClient(api_key=os.environ["ANTHROPIC_API_KEY"])


def generate_sql(question: str, context: dict) -> dict:
    return _from_env().generate_sql(question, context)


def write_answer(question: str, sql: str, rows: list[dict]) -> dict:
    return _from_env().write_answer(question, sql, rows)
