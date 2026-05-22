# ✅ Co-pilot Checklist — από το μηδέν σε live URL

Σου έχω ήδη φτιάξει: bcrypt hashes, cookie key, και template του secrets.toml με τα πάντα ΕΚΤΟΣ από 4 πεδία που μόνο εσύ μπορείς να βρεις.

Τα 4 πεδία που λείπουν:
- `GEMINI_API_KEY`
- `METABASE_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`

Όλα τα άλλα είναι έτοιμα στο [SECRETS_TO_PASTE.toml](SECRETS_TO_PASTE.toml).

---

## ⏱ Συνολικός χρόνος: ~25 λεπτά

### Βήμα 1 — Supabase project (5')

1. [supabase.com/dashboard](https://supabase.com/dashboard) → **Sign in** (GitHub OAuth ή email)
2. **New project**:
   - Name: `myteam-ai-agent`
   - Database password: όποιο θες (κράτα το)
   - Region: **Frankfurt (eu-central-1)** ή **London**
3. Περίμενε ~2 λεπτά να σηκωθεί
4. **SQL Editor** → **+ New query** → paste όλο το `supabase/migrations/001_init.sql` → **Run** (κάτω δεξιά)
   - Θα δεις: "Success. No rows returned."
5. **Settings → API** (αριστερό μενού, κάτω):
   - Πάρε **Project URL** → π.χ. `https://abcdef.supabase.co`
   - Πάρε **service_role** key (όχι anon!) → `eyJhbGc...` (πατάς "Reveal")

**→ Σημείωσε τα για το βήμα 4.**

---

### Βήμα 2 — Gemini API key (1')

1. [aistudio.google.com/apikey](https://aistudio.google.com/apikey) → **Create API key**
2. Πάρε το key → `AIza...`

**→ Σημείωσε το για το βήμα 4.**

---

### Βήμα 3 — Metabase API key (2')

Στο Metabase της myTeam:
1. **Admin Settings** (γρανάζι πάνω δεξιά) → **Settings** → **Authentication** → **API Keys** → **Create API Key**
2. Name: `myteam-ai-agent`
3. Group: επίλεξε ένα group που έχει access στη myTeam DB (π.χ. Administrators ή custom)
4. **Create** → Copy το key (`mb_...` ή similar). **Εμφανίζεται μόνο μία φορά.**

**→ Σημείωσε το για το βήμα 4.**

Επίσης, σημείωσε το **Database ID** της myTeam DB:
- Πάνε σε οποιοδήποτε question/dashboard που χρησιμοποιεί τη βάση → δες το URL: `/browse/<DB_ID>-...`
- Ή Admin → Databases → click στο name → στο URL: `/admin/databases/<DB_ID>`

Αν είναι **2**, τα έχεις ήδη pre-filled. Αν είναι άλλο, θα το αλλάξεις στο βήμα 4.

---

### Βήμα 4 — Συμπλήρωσε το SECRETS_TO_PASTE.toml (2')

```bash
cd ~/Desktop/myteam-ai-agent-mvp
open -a TextEdit SECRETS_TO_PASTE.toml
```

Αντικατάστησε 4 (ή 5) γραμμές:

```toml
GEMINI_API_KEY = "AIza..."                                # από βήμα 2
METABASE_API_KEY = "mb_..."                               # από βήμα 3
METABASE_DATABASE_ID = "2"                                # αλλάζεις αν χρειάζεται
SUPABASE_URL = "https://abcdef.supabase.co"               # από βήμα 1
SUPABASE_SERVICE_KEY = "eyJhbGc..."                       # από βήμα 1
```

Save. **ΜΗΝ το κάνεις commit** (είναι ήδη στο .gitignore).

---

### Βήμα 5 — Local test (3')

Πριν πας online, βεβαιώσου ότι δουλεύει τοπικά:

```bash
cd ~/Desktop/myteam-ai-agent-mvp
source .venv/bin/activate
pip install -r requirements.txt

# Copy το secrets σε σωστή θέση
cp SECRETS_TO_PASTE.toml .streamlit/secrets.toml

# Σήκωσε το app
streamlit run streamlit_app.py
```

Θα ανοίξει στο **http://localhost:8501**.

**Login:**
- Username: `dimitris`
- Password: **`yPH7BFWfIcWN9hkh`**

> ⚠️ Σημείωσέ το κάπου ασφαλές. Είναι το μόνο σημείο όπου σου το λέω σε plaintext.

Πάτα **Test Gemini** + **Test Metabase** + **Test Supabase** στο sidebar (μόνο για admins). Πρέπει και τα 3 να γίνουν πράσινα.

Δοκίμασε μία ερώτηση: «πόσοι ενεργοί αθλητές υπάρχουν στο club;»

---

### Βήμα 6 — Git push σε GitHub (3')

```bash
cd ~/Desktop/myteam-ai-agent-mvp

# Clean any broken .git folder από τη μεταφορά
rm -rf .git

# Init + commit
git init -b main
git add .
git commit -m "myteam ai agent — phase A+B"

# Φτιάξε private repo (γρηγορότερο με GitHub CLI)
brew install gh                            # αν δεν έχεις
gh auth login                              # μία φορά
gh repo create myteam-ai-agent --private --source=. --push
```

Αν δεν έχεις `gh`:
1. github.com → **New repository** → name: `myteam-ai-agent` → **Private** → **Create**
2. Στο τέρμινάλ:
   ```bash
   git remote add origin git@github.com:YOUR_USERNAME/myteam-ai-agent.git
   git branch -M main
   git push -u origin main
   ```

**Επιβεβαίωσε ότι το `SECRETS_TO_PASTE.toml` και το `.streamlit/secrets.toml` ΔΕΝ είναι στο repo** (έχουν αγνοηθεί από `.gitignore`):
```bash
git ls-files | grep -i secret  # δεν πρέπει να επιστρέψει τίποτα
```

---

### Βήμα 7 — Streamlit Cloud deployment (5')

1. [share.streamlit.io](https://share.streamlit.io) → **Sign in with GitHub** → authorize
2. **New app**:
   - Repository: `YOUR_USERNAME/myteam-ai-agent`
   - Branch: `main`
   - Main file path: `streamlit_app.py`
3. **Advanced settings** → Python version: **3.11**
4. **Secrets** field → άνοιξε το `SECRETS_TO_PASTE.toml` τοπικά → **Copy All** → paste εδώ
5. **Deploy**

Περίμενε ~2-3 λεπτά. Όταν τελειώσει:
- Παίρνεις URL: `https://myteam-ai-agent-XXXX.streamlit.app`
- Login με `dimitris` / `yPH7BFWfIcWN9hkh`

---

## 🎉 Έτοιμο

Αυτή η εφαρμογή έχει:
- ✅ Live URL με auth
- ✅ Credentials προ-αποθηκευμένα (όχι χρειάζεται κάθε φορά)
- ✅ Supabase logging: κάθε ερώτηση + SQL + απάντηση + feedback
- ✅ 👍/👎/✏️ Edit SQL buttons
- ✅ Admin page με ιστορικό, ⭐ golden examples, 📝 schema annotations

---

## 🚧 Αν κολλήσεις

Στείλε μου:
- σε ποιο βήμα είσαι
- screenshot ή error text

Και θα σε ξεμπλοκάρω άμεσα.

## 📋 Cheat sheet (passwords που έφτιαξα)

```
admin user:
  username: dimitris
  password: yPH7BFWfIcWN9hkh

regular user:
  username: manager_glyfada
  password: I88ATjI6Wj4TjWBK

cookie_key: TCnSNqgXQxkN_hoJjgDPBxhFBHY1FLicvrMkJmMeH70
```

**Σημείωση**: αν θες να αλλάξεις τα passwords σε δικά σου επιλογής:
```bash
python scripts/make_password_hash.py "MyNewPassword"
# → copy το hash και αντικατέστησέ το στο SECRETS_TO_PASTE.toml
```
