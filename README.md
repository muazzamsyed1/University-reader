# Study Abroad Automation Platform

Admin platform that stores university programmes per country and runs a
"requirement reader" which visits each programme's page, extracts the current
admission criteria (status, dates, fees, CGPA, English requirement) and proposes
the differences for approval.

FastAPI (async) + PostgreSQL + a single-file dashboard.

---

## Project layout

```
app/
  main.py                  FastAPI app, serves the dashboard
  core/                    config, database engine, security, first-boot setup
  modules/auth/            login, JWT, admin accounts
  modules/countries/       catalogue, reader, Excel import/export, chatbot
frontend/index.html        the whole admin dashboard (no build step)
database/
  study_abroad_data.sql    snapshot of the catalogue (no accounts, no API keys)
  export_db.py             re-export that snapshot from a live database
  rebuild.py               rebuild the database from the Excel workbooks
countriesEXCELsheetsData/  source admission workbooks
```

---

## Run locally

```powershell
cd d:\ReaderSH
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

Open <http://127.0.0.1:8000/dashboard>.

Settings come from a `.env` file in the project root — copy `.env.example` to
`.env` and fill it in.

Restore the catalogue into an empty local database:

```powershell
psql -h localhost -U postgres -d study_abroad_db -f database/study_abroad_data.sql
```

---

## Deploy (Railway)

Start command (already in `Procfile` and `railway.json`):

```
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

### Environment variables

Required:

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (Railway fills this in) |
| `SECRET_KEY` | long random string — signs logins **and** encrypts stored API keys, so never change it after go-live |
| `ADMIN_SUPERUSER_EMAIL` | the email you sign in with |
| `ADMIN_SUPERUSER_PASSWORD` | the password you sign in with |

Recommended:

| Variable | Value |
| --- | --- |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `720` |
| `AUTO_IMPORT_ON_EMPTY` | `true` — seeds an empty database from the workbooks on first boot |
| `LOG_LEVEL` | `INFO` |

Optional (AI extraction; keys can also be added from Dashboard → API Keys):

| Variable | Value |
| --- | --- |
| `AI_PROVIDER` | `gemini`, `openai`, `claude` or `llama` |
| `GEMINI_API_KEY` | your key |

Do **not** set `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` or `DB_PASSWORD` —
`DATABASE_URL` replaces all five. Do not set `PORT`; the platform provides it.

A healthy first boot logs:

```
Seeded super-admin: <your email>
Auto-imported data: ... countries, ... universities, ... programmes.
Application startup complete.
```

---

## Notes

* The reader needs long-running background work, so a serverless host
  (e.g. Vercel) is not suitable — use Railway, Render, Fly.io or a VPS.
* `.env`, private workbooks containing student data, and the virtual
  environment are excluded from version control; see `.gitignore`.
