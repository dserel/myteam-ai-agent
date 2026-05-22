# myTeam AI Agent — MVP

Text-to-SQL agent για myTeam: ο χρήστης ρωτάει στα ελληνικά μέσα σε **Streamlit chat UI**, το Gemini Flash παράγει SQL, ο validator το ελέγχει, το Metabase το τρέχει, το Gemini απαντάει.

> ⚠️ **Status: INTERNAL DEMO ONLY.** Δεν είναι production-ready ως προς tenant isolation.

## Δομή

```
myteam-ai-agent-mvp/
├── README_MVP.md
├── requirements.txt
├── streamlit_app.py             ← chat UI (Streamlit)
├── schema_pruned.md             ← context που τρέφει το LLM
├── .gitignore
├── .streamlit/
│   └── secrets.toml.example     ← copy σε secrets.toml και συμπλήρωσε
├── prompts/
│   ├── sql_generator.md         ← system prompt για text-to-SQL
│   └── answer_writer.md         ← system prompt για το second pass
├── src/
│   ├── __init__.py
│   ├── agent.py                 ← FastAPI /ask endpoint (εναλλακτικό για embed)
│   ├── llm.py                   ← LLMClient (Gemini wrapper)
│   ├── metabase.py              ← MetabaseClient
│   └── validator.py             ← SQL guardrails (sqlparse)
└── detectors/
    ├── runner.py                ← cron entrypoint
    ├── churn_risk.sql
    ├── mom_revenue.sql
    └── expiring_subscriptions.sql
```

Δύο διαφορετικά "frontends" στον ίδιο πυρήνα:

| | Streamlit (recommended για demo) | FastAPI (για embed) |
|---|---|---|
| Εντολή | `streamlit run streamlit_app.py` | `uvicorn src.agent:app --port 8080` |
| Config | Sidebar inputs | Env vars |
| Καλό για | Demo, internal use | Embed σε υπάρχον myTeam app |

## 🚀 Quick start (local)

```bash
cd myteam-ai-agent-mvp
pip install -r requirements.txt

# Self-test του validator (κανένα API call)
python src/validator.py
# → 8/8 tests passed

# Σήκωσε το Streamlit
streamlit run streamlit_app.py
# → ανοίγει http://localhost:8501
```

Στο sidebar συμπλήρωσε:

| Field | Τι είναι |
|---|---|
| **Gemini API Key** | https://aistudio.google.com/apikey (free tier επαρκές για demo) |
| **Metabase URL** | π.χ. `https://metabase.myteam.gr` |
| **Metabase API Key** (recommended) | Metabase → Admin Settings → Authentication → API Keys → Create API Key. **Δεν λήγει.** |
| **Metabase Session Token** (alt) | `curl -X POST $URL/api/session -H 'Content-Type: application/json' -d '{"username":"...","password":"..."}'` → πάρε το `id`. **Λήγει σε ~14 μέρες.** |
| **Metabase Database ID** | Στο Metabase Admin → Databases, στο URL της myTeam DB φαίνεται το id |
| **Role** | `manager` ή `parent` |
| **Club ID** | π.χ. 41 για ΓΛΥΦΑΔΑ Ε.Σ. |
| **User ID** | Απαιτείται μόνο για role=parent |

Πάτα **Test Gemini** και **Test Metabase** στο sidebar για επιβεβαίωση πριν ρωτήσεις.

## ☁️ Deploy στο Streamlit Cloud (free)

1. **Push το `myteam-ai-agent-mvp/` σε GitHub repo.**
   ```bash
   cd myteam-ai-agent-mvp
   git init
   git add .
   git commit -m "myteam ai agent mvp"
   gh repo create myteam-ai-agent --private --source=. --push
   ```
   Το `.gitignore` βγάζει το `secrets.toml` — δεν θα διαρρεύσουν credentials.

2. **Πήγαινε στο [share.streamlit.io](https://share.streamlit.io)** και συνδέσου με τον GitHub λογαριασμό σου.

3. **New app** → επίλεξε το repo → Main file path: `streamlit_app.py` → **Advanced settings** → Python version 3.11.

4. **Secrets** (Advanced settings → Secrets): paste το περιεχόμενο του `secrets.toml.example` με τις πραγματικές τιμές:
   ```toml
   GEMINI_API_KEY = "AIza..."
   METABASE_URL = "https://metabase.myteam.gr"
   METABASE_API_KEY = "mb_..."        # preferred (δεν λήγει)
   # METABASE_SESSION = "..."          # εναλλακτικό (λήγει)
   METABASE_DATABASE_ID = "2"
   DEFAULT_CLUB_ID = "41"
   ```

5. **Deploy**. Σε ~2 λεπτά παίρνεις URL `https://myteam-ai-agent-xxxx.streamlit.app`.

> 💡 **Tip**: τα secrets prefilled στο sidebar αλλά είναι editable — άρα ο χρήστης μπορεί να αλλάξει role/club_id χωρίς να ξανακάνει deploy.

> 💡 **Χρησιμοποίησε API Key αντί για Session Token** — δεν λήγει ποτέ, οπότε δεν χρειάζεται renewal κάθε 14 μέρες.

## 🤖 Πώς δουλεύει το pipeline

```
User question (Streamlit chat)
  ▼
[1] llm.generate_sql(question, context)        ← Gemini Flash + sql_generator.md + schema_pruned.md
      → JSON {sql, rationale, ...}
  ▼
[2] validator.validate_and_normalize(sql, ...)
      → only SELECT
      → has club_id = <tenant>
      → if parent: has parent_users.parent_id = <user_id>
      → auto-inject LIMIT 1000
  ▼
[3] metabase.run_sql(safe_sql)                  ← POST /api/dataset
  ▼
[4] llm.write_answer(question, sql, rows)       ← Gemini Flash + answer_writer.md
  ▼
Streamlit chat: answer + collapsible SQL + collapsible rows table
```

## 🔮 Proactive insights (nightly cron) — optional

```bash
# Manual run για 1 club
python -m detectors.runner --today 2026-05-20 --club 41

# Cron (κάθε νύχτα 03:15)
15 3 * * * cd /opt/myteam-ai-agent && /usr/bin/python -m detectors.runner
```

> ℹ️ Στο Streamlit Cloud δεν τρέχεις cron. Για proactive insights θέλεις:
> - **GitHub Actions** scheduled workflow (free, εύκολο), ή
> - **Render/Railway** cron job, ή
> - Cron σε δικό σου server

3 detectors προς το παρόν:

| Key | Τι ψάχνει | Severity |
|---|---|---|
| `churn_risk` | Ενεργοί αθλητές με 0 παρουσίες σε 30 μέρες αλλά παρουσίες στις προηγούμενες 60 | alert |
| `mom_revenue` | Μεταβολή εσόδων μήνα vs προηγούμενο, με threshold ≥ 10% | warn |
| `expiring_subscriptions` | Συνδρομές που λήγουν στις επόμενες 14 μέρες | warn |

Τα ευρήματα θα γράφονται σε νέο πίνακα `agent_insights` (DDL στο `runner.py`).
**Το MVP τα κάνει μόνο log** — η εγγραφή σε DB θέλει ξεχωριστό write connection.

## 🧠 Πώς αλλάζεις/βελτιώνεις τη συμπεριφορά

- **Πρόσθεσε few-shot example**: άνοιξε `prompts/sql_generator.md`, πρόσθεσε νέο "Παράδειγμα N" με την ερώτηση + το expected JSON output.
- **Πρόσθεσε πίνακα στο σχήμα**: άνοιξε `schema_pruned.md`, γράψε CREATE TABLE summary + σχόλια. Το LLM θα το ξέρει αμέσως.
- **Αλλαγή απάντησης**: `prompts/answer_writer.md` — π.χ. αν θες πιο τυπικό tone, αλλάζεις εκεί.
- **Νέος detector**: copy ένα `.sql` στο `detectors/`, ο runner τον σηκώνει αυτόματα.
- **Αυστηρότερο validation**: `src/validator.py` — π.χ. ban specific tables.

## ⚖️ Tradeoffs & limitations (MVP)

| Issue | Status |
|---|---|
| Tenant isolation βασίζεται σε prompt + regex validator | ⚠ Επαρκές για internal demo, ΟΧΙ production |
| Metabase user έχει πλήρες read στη DB | Πρέπει να περιοριστεί σε whitelist tables πριν beta |
| Statement timeout | Πρέπει να οριστεί σε Metabase admin (10s suggested) |
| Cost monitoring | Όχι ακόμα — προσθήκη token counter για ανά-club spending |
| Schema staleness | Hard-coded — αν αλλάξει DB schema, ενημερώνεις χειροκίνητα το `schema_pruned.md` |
| Hallucination guard | Μόνο validator. Δεν γίνεται "did the SQL actually answer the question" check |
| Multilingual | Μόνο ελληνικά. Για αγγλικά, διπλασίασε τα prompts |
| Chat history persistence | In-memory (Streamlit session) — χάνεται στο refresh |

## 🔒 Security checklist για production

- [ ] Ξεχωριστός Metabase user + permissions group με access ΜΟΝΟ σε whitelist tables
- [ ] Metabase admin: query timeout 10s, max rows 10k
- [ ] Read-only MySQL user στη βάση (BACKUP του Metabase service user)
- [ ] Rate limiting per user (π.χ. 30/min)
- [ ] Audit log: κάθε question + generated SQL + execution time → πίνακας `agent_audit`
- [ ] Cost cap per club per day (token budget)
- [ ] Test suite με γνωστές "evil" ερωτήσεις (prompt injection, SQL injection μέσω LLM)
- [ ] Auth για το ίδιο το app (Streamlit native ή reverse proxy)
- [ ] Pen-test από third party

## 🛣️ Roadmap (priority order)

1. **Δοκίμασε με 10-20 πραγματικές ερωτήσεις** από έναν manager — κατάγραψε τι σπάει
2. **Επιβεβαίωσε τα ENUMs** στο schema (users.role, events.type, eventable_type) — άλλαξε το `schema_pruned.md`
3. **Πρόσθεσε auth** στο Streamlit app (`streamlit-authenticator` package, ή reverse proxy με Basic Auth)
4. **Δημιούργησε `agent_insights` table** στη myTeam DB και activate τους detectors με write connection
5. **Tenant lockdown**: Metabase permission group, query timeout, rate limit
6. **Embed στο myTeam app**: αλλαγή από Streamlit σε `agent.py` FastAPI + iframe ή custom React widget
