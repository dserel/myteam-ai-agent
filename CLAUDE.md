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
- `streamlit_app.py` — main chat UI (auth, sidebar role/club, retry loop, streaming, feedback, starter buttons, follow-up chips). Reads `st.session_state["_pending_prompt"]` set by starter/follow-up buttons (incl. from other pages via `st.switch_page`).
- `src/llm.py` — Claude wrapper. Methods: `generate_sql`, `fix_sql_after_error`, `rewrite_followup` (conversational memory), `write_answer`/`stream_answer`, `write_briefing`, `draft_parent_message`, `event_tips` (per-event parent heads-up). **Prompt caching** (cache_control ephemeral) on the static SQL prefix + answer-writer + briefing/draft/tips system prompts. Default model = Haiku. 429s return a clean Greek message; client has max_retries=3.
- `src/metabase.py` — Metabase `/api/dataset` wrapper. `run_sql()` converts ALL errors (timeout/HTTP/JSON) into `MetabaseError` so the app never hard-crashes; timeout 40s.
- `src/storage.py` — Supabase wrapper (feedback loop).
- `src/parent_view.py` — **portable, UI-agnostic data layer** for the parent experience: `child_profiles`, `upcoming_events`, `attendance`, `dues`, `milestones`, `schedule_changes`, `club_city`. Centralizes the household-scoped SQL so it can be lifted into the myTeam app (Laravel) or wrapped as an API.
- `src/enrich.py` — **non-myTeam enrichment** (portable): OpenWeather 5d/3h forecast (`attach_weather` single-city, `attach_weather_best` with city-fallback chain returning `(events, used_city)`) + `.ics` builder with VALARM reminders (`build_ics`, 120' for busy/central locations else 90').
- `src/validator.py`, `src/charts.py`, `src/auth.py` (bcrypt hardcoded users in secrets).
- `prompts/sql_generator.md` — SQL system prompt. Split: static prefix (rules+schema+few-shot, **cached**) + dynamic block (context/golden/annotations) built in code by `_render_sql_prompt`.
- `prompts/answer_writer.md` — answer + chart + followups spec (fully static, cached).
- `schema_pruned.md` — pruned myTeam schema for the LLM. **Contains the confirmed enums (see below).**
- `pages/01_Admin.py` — query history, golden examples, schema annotations.
- `pages/02_Alerts.py` — admin club-ops dashboard: 📋 Briefing (LLM narrative + feature-adoption gaps), 🚨 Alerts (churn/expiring/overdue/dormant/athletes-without-parent), 📈 Trends, 🏐 Teams & Events, 💶 Financials (collection rate, value-at-risk), 🎂 Extras. Builds `LLMClient` un-cached. All SQL validated against prod.
- `pages/03_Parent.py` — «Το παιδί μου» parent glance dashboard: identity card, milestones, attendance, dues, weekly schedule with **weather + smart heads-up**, **.ics download**, **schedule-change banner**, follow-up chips (hand off to chat). Thin renderer over parent_view + enrich.
- `detectors/*.sql` + `detectors/runner.py` — churn_risk, expiring_subscriptions, mom_revenue.

## CRITICAL data-model facts (confirmed against production, club 41 = AO Glyfada basketball)
- `users.role` (tinyint): **1=admin, 2=manager, 3=coach, 4=athlete, 5=parent**. Also detect coaches via `team_user.first_coach=1`, athletes via `first_coach=0`.
- `events.type`: **1=training, 2=match (only type with result/score), 3/4=other**.
- `appearance_events.check`: **NOT 0/1** — values 1..6. Use `check=1` for "present", `check<>1 AND check IS NOT NULL` for absent. `appearance_events.status` is unused (null).
- `eventables.eventable_type`: literally **'teams'** or **'users'** (NOT Laravel morph strings). `eventable_id` = teams.id or users.id. Event<->team join: `JOIN eventables ev ON ev.event_id=e.id AND ev.eventable_type='teams' JOIN teams t ON t.id=ev.eventable_id`.
- `teams.category`: `c`/`m`/`f` (not `mx`).
- `payments.payment_method`: **almost always NULL** — never group/filter on it.
- `subscriptions.status`: 1=active, 2=archived.
- `events` has `created_at`, `updated_at`, `deleted_at` (soft delete). Cancellations = `deleted_at` set; edits = `updated_at` >> `created_at`.
- **Family/parent linkage**: primarily via shared `users.household_id` (parent role=5 + athlete role=4 in same household). `parent_users` is OFTEN EMPTY. Parent isolation scopes children via `household_id UNION parent_users`. Example: parent "Dimitris Sereleas" id 143441 -> child "Aris Sereleas" 143442 via household 54454 (no parent_users row). Reserved word: alias `change` as `change_type`.
- **Churn detector**: groups by `user_id` in `detectors/churn_risk.sql` → 1 row per at-risk athlete. Multiple active subscriptions get joined into `subscription_title` as `' / '`-separated (e.g. `"Basic / Extra"`). Previously over-counted (232 vs 41 distinct).
- **Auto weather city**: `pages/03_Parent.py` resolves the OpenWeather city via `pv.club_city(mb, club_id)` (reads `clubs.city`), then falls back to the `WEATHER_CITY` secret via `enrich.attach_weather_best(...)`. The actually-used city is exposed in the page caption.

## Supabase golden_examples (injected into the SQL prompt per role; auto-added on 👍)
Seeded & validated: 4 parent (events-this-week, team/coach, attendance, dues), 3 coach (team schedule, low attendance, roster), 1 manager. All use the confirmed household pattern / enums. Adding more here is the main lever to keep Haiku SQL correct.

## Infra
- GitHub: `dserel/myteam-ai-agent`, branch `main`. Streamlit Cloud auto-deploys on push but **do a full Reboot (Manage app -> Reboot)** after changes that add methods/requirements (auto-redeploy can keep stale `@st.cache_resource` objects — that's why `LLMClient` is built un-cached).
- Supabase project (feedback loop): ref `tvwsbortgawnyflnopsq`, org `ClubGPT`. Migrations `001_init`, `002_add_coach_role`. RLS intentionally off (service_role backend access).
- Metabase: connected DB id **2** ("AWS production readonly", MySQL).
- LLM: Anthropic, **low rate-limit tier (~10k input tokens/min)** -> use **Haiku** (`claude-haiku-4-5-20251001`); Sonnet 429s without a tier upgrade. Prompt caching mitigates this.
- Weather: OpenWeather (5-day/3h forecast). No maps/traffic connector available yet.

## Secrets (Streamlit -> Settings -> Secrets) — names only, never commit values
`ANTHROPIC_API_KEY`, `CLAUDE_MODEL` (=claude-haiku-4-5-20251001), `METABASE_URL` (https://myteam.metabaseapp.com), `METABASE_API_KEY`, `METABASE_DATABASE_ID` (=2), `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `DEFAULT_CLUB_ID` (=41), `OPENWEATHER_API_KEY`, `WEATHER_CITY` (=Glyfada,GR), and `[auth]` with bcrypt `password_hash` per user. See `.streamlit/secrets.toml.example`.

## Design intent
Parent/coach value features are meant to **eventually plug into the existing myTeam app** (Laravel/MySQL). Keep new data logic in reusable modules (`src/parent_view.py`, `src/enrich.py`) — pure SQL/functions — with Streamlit pages as thin renderers, so the SQL can be ported or wrapped as an API.

## Conventions
- **Always validate new SQL against the real DB (via Metabase) before baking it into the app.** Use confirmed enums above.
- Greek UI throughout. Money format EUR 1.234; percentages 12,3%.
- Keep changes tenant-scoped to a club; club 41 is the default test club.
- Self-contained files; don't add deps lightly (requirements.txt).

## Possible next steps (not yet done)
- Maps/traffic integration (real leave-by) — needs a Maps API key.
- One-click "invite/link parent" flow for athletes without a linked parent (~16 in club 41).
- Daily push (email/Slack) of the briefing in addition to the live page / artifact.
- Semantic retrieval (embeddings) for golden examples instead of recent-N.
