# 🚀 Deployment Guide — myTeam AI Agent

Από local σε **live URL με auth + feedback storage** σε ~30 λεπτά.

## Επισκόπηση

```
[Streamlit Cloud (free)] ←→ [Supabase Postgres (free)] ←→ [Metabase + Gemini]
   ↑                            ↑
   Public URL                   query_history, feedback, golden_examples
   με auth gate
```

---

## Βήμα 1: Supabase project (5')

1. Πήγαινε στο **https://supabase.com/dashboard** → **New project**.
2. Δώσε:
   - **Name**: `myteam-ai-agent`
   - **Database Password**: (αποθήκευσέ το, θα το χρειαστείς)
   - **Region**: Frankfurt ή Ireland (πιο κοντά σε Ελλάδα)
3. Περίμενε ~2 λεπτά να σηκωθεί.
4. **SQL Editor** → **New query** → paste το περιεχόμενο του `supabase/migrations/001_init.sql` → **Run**.
   Θα φτιαχτούν 3 πίνακες: `query_history`, `golden_examples`, `schema_annotations`.
5. **Settings → API**:
   - Σημείωσε το **Project URL** (`https://xxxx.supabase.co`)
   - Σημείωσε το **service_role key** (όχι anon — γράφουμε από backend)

> ⚠️ Το `service_role` key bypasses Row Level Security. Μην το βάλεις ποτέ σε client-side κώδικα. Εμείς το έχουμε μόνο στα Streamlit Cloud secrets.

---

## Βήμα 2: Auth — bcrypt passwords (2')

Local στο folder:

```bash
cd ~/Desktop/myteam-ai-agent-mvp
source .venv/bin/activate
pip install bcrypt
python scripts/make_password_hash.py "DimitrisPass123"
# → $2b$12$abcdefg.....
```

Επανάλαβε για κάθε χρήστη που θες (manager_glyfada, κλπ).

---

## Βήμα 3: GitHub repo (5')

```bash
cd ~/Desktop/myteam-ai-agent-mvp

# Init git (αν δεν είναι ήδη)
git init
git add .
git commit -m "myteam ai agent — phase A+B"

# Φτιάξε repo και push (απαιτεί gh CLI ή κάνε το manual στο github.com)
gh repo create myteam-ai-agent --private --source=. --push

# ή χειροκίνητα:
#   1. github.com → New repo (private) → myteam-ai-agent
#   2. git remote add origin git@github.com:USER/myteam-ai-agent.git
#   3. git branch -M main && git push -u origin main
```

> Το `.streamlit/secrets.toml` αγνοείται από το `.gitignore` — μόνο το `.example` πάει στο repo.

---

## Βήμα 4: Streamlit Cloud deployment (5')

1. Πήγαινε στο **https://share.streamlit.io** → **Sign in with GitHub**.
2. **New app**:
   - Repository: επίλεξε το `myteam-ai-agent`
   - Branch: `main`
   - Main file path: `streamlit_app.py`
   - **Advanced settings** → Python version: **3.11**
3. Στο **Secrets** field, paste το ολόκληρο secrets.toml με τις πραγματικές τιμές (πρότυπο από `.streamlit/secrets.toml.example`):

   ```toml
   GEMINI_API_KEY = "AIza..."

   METABASE_URL = "https://metabase.myteam.gr"
   METABASE_API_KEY = "mb_..."
   METABASE_DATABASE_ID = "2"

   SUPABASE_URL = "https://xxxx.supabase.co"
   SUPABASE_SERVICE_KEY = "eyJ..."

   DEFAULT_CLUB_ID = "41"

   [auth]
   cookie_name = "myteam_ai_agent_auth"
   cookie_key = "random-32-char-string-foobarbaz12345"
   cookie_expiry_days = 7

   [[auth.users]]
   username = "dimitris"
   name = "Dimitris"
   role = "admin"
   password_hash = "$2b$12$ΤΟ-HASH-ΑΠΟ-BAΗΜΑ-2"

   [[auth.users]]
   username = "manager_glyfada"
   name = "Manager Γλυφάδας"
   role = "user"
   password_hash = "$2b$12$..."
   ```

4. **Deploy**. Σε ~2 λεπτά παίρνεις URL: `https://myteam-ai-agent-XXXX.streamlit.app`.

5. Επισκέψου το URL → δες το login screen → μπες με τα credentials σου.

---

## Βήμα 5: Δοκίμασέ το (5')

Login → στο sidebar:
- Role: `manager`
- Club ID: `41` (ή ό,τι είναι το σωστό)

Στο chat:
> "Πόσοι νέοι αθλητές μπήκαν φέτος;"

Θα δεις:
1. SQL που παρήγαγε
2. Rows που γύρισε
3. Φυσική απάντηση
4. 👍/👎/✏️ buttons

Πάτα **👍** σε καλές απαντήσεις. Πάτα **✏️ Edit SQL** σε λάθος για να διορθώσεις.

---

## Βήμα 6: Admin page

Στο sidebar πάνω, βλέπεις 2 σελίδες:
- `streamlit_app` (main chat)
- `01 Admin` ← μόνο για admins

Στο **01 Admin**:
- **📜 Query history**: όλες οι ερωτήσεις χρηστών, filter feedback, mark as ⭐ golden
- **⭐ Golden examples**: curated library — αυτά θα χρησιμοποιηθούν σε Φάση Γ ως RAG context
- **📝 Schema annotations**: σημειώσεις για το LLM (π.χ. "team_user.created_at αναξιόπιστο")

---

## Συντήρηση

**Update κώδικα:**
```bash
git add . && git commit -m "update X" && git push
# Streamlit Cloud auto-redeploys
```

**Νέο user:**
1. `python scripts/make_password_hash.py "NewPass"`
2. Πρόσθεσε `[[auth.users]]` block στα Streamlit Cloud Secrets
3. **Manage app → Reboot**

**Backup query history:**
Στο Supabase → SQL Editor:
```sql
COPY (SELECT * FROM query_history) TO STDOUT WITH CSV HEADER;
-- ή κατέβασε CSV από Table Editor
```

**Metabase API key expired/leaked:**
1. Metabase Admin → API Keys → revoke + create new
2. Update στα Streamlit Cloud Secrets → Reboot

---

## Troubleshooting

| Σύμπτωμα | Διάγνωση |
|---|---|
| Login screen δεν εμφανίζεται | Δεν έβαλες `[auth]` section στα secrets |
| Login αποτυγχάνει | Λάθος bcrypt hash. Βεβαιώσου ότι έκανες copy ΟΛΟΚΛΗΡΟ τον hash (αρχίζει με `$2b$`) |
| "Λείπουν secrets" | Δεν έχεις βάλει GEMINI_API_KEY / METABASE_*. Edit στο app settings |
| 👍/👎 δεν αποθηκεύονται | Δεν έχεις βάλει SUPABASE_URL/KEY ή δεν έτρεξες το migration SQL |
| Admin page error | Ο user δεν είναι `role = "admin"` στα secrets |
| Session token expired | Άλλαξε σε API KEY (δεν λήγει) — δες README_MVP.md |
