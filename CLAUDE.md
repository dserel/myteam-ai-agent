# CLAUDE.md — myTeam AI Agent

Context handoff for Claude Code. Read this first; it captures the project, the
data-model gotchas we confirmed against production, and the work done so far.

## What this is
A Streamlit app: a **text-to-SQL AI agent** that lets a sports-club manager / parent /
coach ask questions in Greek about their myTeam data. Flow:
1. `LLMClient.generate_sql()` (Claude) turns the question -> MySQL SELECT.
2. `validator.py` enforces safety (SELECT-only, tenant scoping, LIMIT).
3. `MetabaseClient.run_sql()` runs it against the **myTeam production DB via Metabase**
   `/api/dataset` (read-only, `METABASE_DATABASE_ID=2`).
4. `LLMClient.stream_answer()` (Claude) writes a Greek answer + optional chart + follow-ups.
5. Supabase stores the feedback loop (query_history, golden_examples, schema_annotations).

The data is NOT in Supabase — it lives in myTeam's MySQL, reached only through Metabase.

## Key files
- `streamlit_app.py` — main chat UI (auth, sidebar role/club, retry loop, streaming, feedback, starter buttons, follow-up chips).
- `src/llm.py` — Claude wrapper. Methods: `generate_sql`, `fix_sql_after_error`, `rewrite_followup` (conversational memory), `write_answer`/`stream_answer`, `write_briefing`, `draft_parent_message`. Uses **prompt caching** (cache_control ephemeral on the static SQL prompt + answer-writer prompt). Default model = Haiku.
- `src/metabase.py` — Metabase `/api/dataset` wrapper. `run_sql()` converts ALL errors (timeout/HTTP/JSON) into `MetabaseError` so the app never hard-crashes; timeout 40s.
- `src/storage.py` — Supabase wrapper (feedback loop).
- `src/validator.py`, `src/charts.py`, `src/auth.py` (bcrypt hardcoded users in secrets).
- `prompts/sql_generator.md` — SQL system prompt. Split: static prefix (rules+schema+few-shot, **cached**) + dynamic block (context/golden/annotations) built in code.
- `prompts/answer_writer.md` — answer + chart + followups spec (fully static, cached).
- `schema_pruned.md` — pruned myTeam schema given to the LLM. **Contains the confirmed enums (see below).**
- `pages/01_Admin.py` — query history, golden examples, schema annotations.
- `pages/02_Alerts.py` — club-ops dashboard: Briefing (LLM narrative + feature-adoption gaps), Alerts (churn/expiring/overdue/dormant/athletes-without-parent), Trends, Teams & Events, Financials (collection rate, value-at-risk), Extras. Admin-gated. All SQL validated against prod.
- `src/parent_view.py` — **portable, UI-agnostic data layer** for the parent experience (child profile, upcoming events, attendance, dues, milestones). Centralizes the household-scoped SQL so it can be lifted into the myTeam app later. `pages/03_Parent.py` («Το παιδί μου») is a thin renderer over it.
- `detectors/*.sql` + `detectors/runner.py` — churn_risk, expiring_subscriptions, mom_revenue.

## CRITICAL data-model facts (confirmed against production, club 41 = AO Glyfada basketball)
- `users.role` (tinyint): **1=admin, 2=manager, 3=coach, 4=athlete, 5=parent**. Also detect coaches via `team_user.first_coach=1`, athletes via `first_coach=0`.
- `events.type`: **1=training, 2=match (only type with result/score), 3/4=other**.
- `appearance_events.check`: **NOT 0/1** — values 1..6. Use `check=1` for "present", `check<>1 AND check IS NOT NULL` for absent. `appearance_events.status` is unused (null).
- `teams.category`: `c`/`m`/`f` (not `mx`).
- `payments.payment_method`: **almost always NULL** — never group/filter on it.
- `subscriptions.status`: 1=active, 2=archived.
- **Family/parent linkage**: primarily via shared `users.household_id` (a parent role=5 + athlete role=4 in the same household). `parent_users` is OFTEN EMPTY. Parent isolation in the SQL prompt scopes children via `household_id UNION parent_users`. Example: parent "Dimitris Sereleas" id 143441 -> child "Aris Sereleas" 143442 via household 54454 (no parent_users row).
- **Known issue**: the churn detector double-counts athletes with multiple subscription rows (232 rows vs 41 distinct). Briefing/money metrics use distinct counts; the raw churn list still over-counts — candidate fix.

## Infra
- GitHub: `dserel/myteam-ai-agent`, branch `main`. Streamlit Cloud auto-deploys on push but **a full Reboot (Manage app -> Reboot) is needed** after changes that add methods or change requirements (auto-redeploy can keep stale `@st.cache_resource` objects).
- Supabase project (feedback loop): ref `tvwsbortgawnyflnopsq`, org `ClubGPT`. Migrations `001_init`, `002_add_coach_role`. RLS intentionally off (service_role backend access).
- Metabase: connected DB id **2** ("AWS production readonly", MySQL).
- LLM: Anthropic. Org is on a **low rate-limit tier (~10k input tokens/min)** -> use **Haiku** (`claude-haiku-4-5-20251001`); Sonnet 429s without a tier upgrade. Prompt caching mitigates this.

## Secrets (Streamlit -> Settings -> Secrets) — names only, never commit values
`ANTHROPIC_API_KEY`, `CLAUDE_MODEL` (=claude-haiku-4-5-20251001), `METABASE_URL` (https://myteam.metabaseapp.com), `METABASE_API_KEY`, `METABASE_DATABASE_ID` (=2), `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `DEFAULT_CLUB_ID` (=41), and `[auth]` with bcrypt `password_hash` per user. See `.streamlit/secrets.toml.example`.

## Conventions
- **Always validate new SQL against the real DB (via Metabase) before baking it into the app.** Use confirmed enums above.
- Greek UI throughout. Money format EUR 1.234; percentages 12,3%.
- Keep changes tenant-scoped to a club; club 41 is the default test club.
- Self-contained files; don't add deps lightly (requirements.txt).

## Design intent
The parent/coach value features are meant to **eventually plug into the existing myTeam app** (Laravel/MySQL). Keep new data logic in reusable modules like `src/parent_view.py` (pure SQL/functions), with Streamlit pages as thin renderers, so the SQL can be ported or wrapped as an API.

## Possible next steps (not yet done)
- Fix churn detector to count distinct athletes (dedupe multi-subscription rows).
- One-click "invite/link parent" flow for the ~16 athletes without a linked parent.
- Daily push (email/Slack) of the briefing in addition to the live Alerts page / artifact.
- Semantic retrieval (embeddings) for golden examples instead of recent-N.
- Polls / online-payments feature-adoption signals.
