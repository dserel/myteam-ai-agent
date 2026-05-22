"""
validator.py
------------
SQL guardrails για το text-to-SQL pipeline.

Έλεγχοι:
  1. Είναι ΕΝΑ statement, τύπου SELECT (όχι DDL/DML/multi-statement).
  2. Δεν περιέχει επικίνδυνες λέξεις-κλειδιά (INTO OUTFILE, LOAD_FILE, κλπ).
  3. Περιέχει tenant filter ταιριαστό με τον ρόλο:
       - manager  → πρέπει να φιλτράρει σε club_id = <tenant_club_id>
       - parent   → πρέπει επιπλέον να φιλτράρει σε parent_users.parent_id = <tenant_user_id>
  4. Αν δεν υπάρχει LIMIT, το επικολλάει αυτόματα.

Χρήση:
    from validator import validate_and_normalize, ValidationError
    safe_sql = validate_and_normalize(
        raw_sql,
        tenant_club_id=42,
        tenant_user_id=4833,
        user_role="manager",
        default_limit=1000,
    )
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlparse
from sqlparse.tokens import DML, Keyword


class ValidationError(Exception):
    pass


# Λέξεις-κλειδιά που ΑΡΝΟΥΜΑΣΤΕ ασχέτως context
_FORBIDDEN_TOKENS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE",
    "RENAME", "GRANT", "REVOKE", "REPLACE", "LOCK", "UNLOCK", "CALL",
    "EXECUTE", "HANDLER", "LOAD",
}

_FORBIDDEN_PATTERNS = [
    re.compile(r"\bINTO\s+OUTFILE\b", re.I),
    re.compile(r"\bINTO\s+DUMPFILE\b", re.I),
    re.compile(r"\bLOAD_FILE\b", re.I),
    re.compile(r"\bSLEEP\s*\(", re.I),
    re.compile(r"\bBENCHMARK\s*\(", re.I),
    # block xp_/sp_/sys_ schemas just in case
    re.compile(r"\binformation_schema\b", re.I),
    re.compile(r"\bmysql\.\w+", re.I),
    re.compile(r"\bperformance_schema\b", re.I),
]


@dataclass
class ValidationResult:
    sql: str
    notes: list[str]


def _strip_comments_and_normalize(sql: str) -> str:
    """Αφαιρεί SQL comments για να μη ξεγλιστράνε filters μέσα σε /* */."""
    no_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    no_line = re.sub(r"--[^\n]*", " ", no_block)
    return no_line.strip().rstrip(";").strip()


def _is_single_select(sql: str) -> bool:
    parsed = sqlparse.parse(sql)
    if len(parsed) != 1:
        return False
    stmt = parsed[0]
    # first DML token must be SELECT (or WITH for CTE)
    for tok in stmt.tokens:
        if tok.ttype is DML and tok.value.upper() == "SELECT":
            return True
        if tok.ttype is Keyword.CTE or (tok.ttype is Keyword and tok.value.upper() == "WITH"):
            return True
        if tok.is_whitespace or tok.ttype in (None,) and not tok.value.strip():
            continue
        # If we see anything before SELECT/WITH, check if it's WITH
        if tok.value.upper().startswith("WITH"):
            return True
        # Anything else first = not a simple SELECT
        if tok.ttype is DML:
            return False
    return False


def _has_forbidden(sql: str) -> tuple[bool, str | None]:
    upper = sql.upper()
    # token-based scan (avoids hitting words inside string literals — best-effort)
    for kw in _FORBIDDEN_TOKENS:
        if re.search(rf"\b{kw}\b", upper):
            # Allow as part of column/table alias only if not at start of clause.
            # Conservative: just block.
            return True, kw
    for pat in _FORBIDDEN_PATTERNS:
        m = pat.search(sql)
        if m:
            return True, m.group(0)
    return False, None


_CLUB_FILTER_PATTERNS = [
    # club_id = 42  /  club_id=42
    re.compile(r"\bclub_id\s*=\s*(\d+)\b", re.I),
    # c.id = 42 inside a WHERE next to clubs alias — best-effort
]

_PARENT_FILTER_PATTERNS = [
    re.compile(r"\bparent_users\b", re.I),  # must at least reference the pivot
]
_PARENT_ID_PATTERNS = [
    re.compile(r"\.parent_id\s*=\s*(\d+)\b", re.I),
    re.compile(r"\bparent_id\s*=\s*(\d+)\b", re.I),
]


def _has_club_filter(sql: str, expected_club_id: int) -> bool:
    for pat in _CLUB_FILTER_PATTERNS:
        for m in pat.finditer(sql):
            if int(m.group(1)) == expected_club_id:
                return True
    return False


def _has_parent_filter(sql: str, expected_parent_id: int) -> bool:
    if not any(p.search(sql) for p in _PARENT_FILTER_PATTERNS):
        return False
    for pat in _PARENT_ID_PATTERNS:
        for m in pat.finditer(sql):
            if int(m.group(1)) == expected_parent_id:
                return True
    return False


def _ensure_limit(sql: str, default_limit: int) -> tuple[str, bool]:
    """Επιστρέφει (sql, added_limit_flag). Δεν αγγίζει αν υπάρχει ήδη LIMIT."""
    if re.search(r"\bLIMIT\s+\d+", sql, re.I):
        return sql, False
    return f"{sql} LIMIT {default_limit}", True


# ----------------------------------------------------------------------------

def validate_and_normalize(
    raw_sql: str,
    *,
    tenant_club_id: int,
    tenant_user_id: int | None,
    user_role: str,
    default_limit: int = 1000,
) -> ValidationResult:
    """Validates and normalizes the SQL. Raises ValidationError on any guardrail miss."""

    if not raw_sql or not raw_sql.strip():
        raise ValidationError("empty SQL")

    sql = _strip_comments_and_normalize(raw_sql)

    if not _is_single_select(sql):
        raise ValidationError("only single SELECT/WITH statements are allowed")

    bad, what = _has_forbidden(sql)
    if bad:
        raise ValidationError(f"forbidden token in SQL: {what}")

    if not _has_club_filter(sql, tenant_club_id):
        raise ValidationError(
            f"missing tenant filter: query must contain `club_id = {tenant_club_id}`"
        )

    notes: list[str] = []

    if user_role == "parent":
        if tenant_user_id is None:
            raise ValidationError("parent role requires tenant_user_id")
        if not _has_parent_filter(sql, tenant_user_id):
            raise ValidationError(
                f"parent role: query must filter through parent_users.parent_id = {tenant_user_id}"
            )

    sql, added = _ensure_limit(sql, default_limit)
    if added:
        notes.append(f"auto-injected LIMIT {default_limit}")

    return ValidationResult(sql=sql + ";", notes=notes)


# ----------------------------------------------------------------------------
# Self-test
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    cases = [
        # (label, sql, role, club_id, user_id, should_pass)
        ("manager simple",
         "SELECT COUNT(*) FROM users WHERE club_id = 42 AND deleted_at IS NULL",
         "manager", 42, None, True),
        ("manager missing club filter",
         "SELECT COUNT(*) FROM users WHERE deleted_at IS NULL",
         "manager", 42, None, False),
        ("manager wrong club id",
         "SELECT COUNT(*) FROM users WHERE club_id = 99",
         "manager", 42, None, False),
        ("forbidden DROP",
         "SELECT 1; DROP TABLE users;",
         "manager", 42, None, False),
        ("forbidden into outfile",
         "SELECT * FROM users WHERE club_id = 42 INTO OUTFILE '/tmp/x'",
         "manager", 42, None, False),
        ("parent ok",
         "SELECT c.id FROM parent_users pu JOIN users c ON c.id = pu.user_id WHERE pu.parent_id = 4833 AND c.club_id = 42",
         "parent", 42, 4833, True),
        ("parent missing parent filter",
         "SELECT * FROM users WHERE club_id = 42",
         "parent", 42, 4833, False),
        ("auto limit",
         "SELECT id FROM users WHERE club_id = 42",
         "manager", 42, None, True),
    ]
    passed = 0
    for label, sql, role, club, uid, should_pass in cases:
        try:
            r = validate_and_normalize(sql, tenant_club_id=club, tenant_user_id=uid, user_role=role)
            ok = should_pass
            print(f"{'✓' if ok else '✗'} {label}: PASS ({r.notes})")
        except ValidationError as e:
            ok = not should_pass
            print(f"{'✓' if ok else '✗'} {label}: REJECT ({e})")
        passed += int(ok)
    print(f"\n{passed}/{len(cases)} tests passed")
