"""
agent.py
--------
FastAPI app εκθέτει ένα endpoint POST /ask που:
  1. Παίρνει question + user context
  2. Καλεί llm.generate_sql → JSON {sql, ...}
  3. Περνά το SQL από validator
  4. Τρέχει στο Metabase
  5. Καλεί llm.write_answer για natural language απάντηση
  6. Επιστρέφει {answer, sql, row_count, insights?}

Εκκίνηση:
    uvicorn src.agent:app --reload --port 8080
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src import llm, metabase
from src.validator import ValidationError, validate_and_normalize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("agent")

app = FastAPI(title="myTeam AI Agent (MVP)", version="0.1.0")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=2)
    tenant_club_id: int
    tenant_user_id: Optional[int] = None
    user_role: str = Field(..., pattern="^(manager|parent)$")
    today: Optional[str] = None  # ISO YYYY-MM-DD


class AskResponse(BaseModel):
    answer: str
    sql: Optional[str] = None
    row_count: Optional[int] = None
    rejected_reason: Optional[str] = None
    notes: list[str] = []


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    ctx = {
        "tenant_club_id": req.tenant_club_id,
        "tenant_user_id": req.tenant_user_id,
        "user_role": req.user_role,
        "today": req.today or date.today().isoformat(),
    }
    log.info("question role=%s club=%s q=%r", req.user_role, req.tenant_club_id, req.question[:120])

    # 1. SQL generation
    try:
        gen = llm.generate_sql(req.question, ctx)
    except Exception as e:
        log.exception("SQL gen failed")
        raise HTTPException(status_code=500, detail=f"sql gen: {e}")

    if "error" in gen:
        return AskResponse(answer=gen["error"], rejected_reason="model_refused")

    raw_sql = gen.get("sql", "").strip()
    if not raw_sql:
        return AskResponse(answer="Δεν μπόρεσα να φτιάξω query για αυτή την ερώτηση.", rejected_reason="empty_sql")

    # 2. Validation
    try:
        v = validate_and_normalize(
            raw_sql,
            tenant_club_id=req.tenant_club_id,
            tenant_user_id=req.tenant_user_id,
            user_role=req.user_role,
        )
    except ValidationError as e:
        log.warning("validation failed: %s\nraw_sql=%s", e, raw_sql)
        return AskResponse(
            answer="Δεν μπόρεσα να φτιάξω ασφαλές query για αυτή την ερώτηση. Δοκίμασε να την αναδιατυπώσεις πιο συγκεκριμένα.",
            sql=raw_sql,
            rejected_reason=f"validator: {e}",
        )

    log.info("validated sql: %s", v.sql)

    # 3. Execution
    try:
        rows = metabase.run_sql(v.sql)
    except metabase.MetabaseError as e:
        log.warning("metabase failed: %s", e)
        return AskResponse(
            answer="Το query τρέξατε αλλά απέτυχε στη βάση. Είναι πιθανώς θέμα δεδομένων.",
            sql=v.sql,
            rejected_reason=f"metabase: {e}",
            notes=v.notes,
        )

    # 4. Answer generation
    try:
        answer = llm.write_answer(req.question, v.sql, rows)
    except Exception as e:
        log.exception("answer gen failed")
        answer = f"Βρήκα {len(rows)} γραμμές αλλά απέτυχε η σύνταξη της απάντησης ({e})."

    return AskResponse(answer=answer, sql=v.sql, row_count=len(rows), notes=v.notes)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}
