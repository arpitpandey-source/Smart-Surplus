# SmartSurplus 🌱

An intelligent food waste reduction and redistribution platform that connects
surplus food donors with eligible recipients.

---

## Features

| Area | What's included |
|---|---|
| **Authentication** | Register, login, logout with hashed passwords, account lockout, session fixation protection, CSRF tokens, CSP headers |
| **Roles** | Donor, Recipient, Admin — role-based access control on every route |
| **Donations** | Create, edit, cancel, delete; track remaining quantity; deadline validation |
| **Discovery** | Search by food name, location; filter by category, quantity, urgency; `v_open_donations` view |
| **Claims** | Atomic quantity reservation, duplicate prevention, single-use claim token, donor accept / reject / complete / cancel lifecycle |
| **Dashboards** | Donor summary (KPIs, incoming requests); Recipient history; Admin platform overview |
| **Admin** | User management with suspension, donation and request monitoring, paginated audit log |
| **Notifications** | In-app persistent notifications with unread badge, mark-read, deduplication |
| **UI/UX** | Responsive CSS (mobile + desktop), accessible forms, status badges, empty states |
| **Database** | Local SQLite (default) **or** Azure SQL Database (via `AZURE_SQL_CONNECTION_STRING`) |
| **Deployment** | Gunicorn + Azure App Service; `startup.sh`, `Procfile`, `gunicorn.conf.py` included |
| **Security** | Parameterised queries throughout, audit log, least-privilege DB user, HTTPS-only cookies in production |

---

## Quick start (local SQLite)

```bash
# 1. Clone
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd SmartSurplus

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Mac / Linux
# .venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) copy the example env file
cp .env.example .env
# Edit .env to set SECRET_KEY; leave AZURE_SQL_CONNECTION_STRING blank for SQLite.

# 5. Run the development server
flask --app app run --debug
```

Open http://127.0.0.1:5000 in your browser.

The database (`instance/smartsurplus_v2.db`) is created automatically on first run.

### Load demo data

```bash
python seed_demo.py
```

This creates a few users, categories, donations and requests so you can explore
the UI immediately.

---

## Running the tests

```bash
python -m unittest discover -s tests -v
```

All 54 tests run against an in-memory SQLite database.

---

## Admin account

After running the app and registering a user:

```bash
flask --app app make-admin you@example.com
```

---

## Azure SQL Database

See **[docs/AZURE_DEPLOYMENT.md](docs/AZURE_DEPLOYMENT.md)** for a full step-by-step beginner guide.

Quick reference:

| Step | Command |
|---|---|
| Copy env template | `cp .env.example .env` |
| Edit `.env` | Set `AZURE_SQL_CONNECTION_STRING` and `APP_ENV=production` |
| Initialise schema (once) | `flask --app app init-azure-db` |
| Run locally against Azure SQL | `flask --app app run` |

Requires **ODBC Driver 18 for SQL Server**: https://aka.ms/odbc18

---

## Production deployment (Azure App Service)

```bash
# Set the startup command on Azure:
#   App Service → Configuration → General settings → Startup command → startup.sh

# Deploy via Local Git:
git remote add azure https://<your-app-name>.scm.azurewebsites.net/<your-app-name>.git
git push azure main
```

Required App Settings on Azure:

| Setting | Value |
|---|---|
| `SECRET_KEY` | Long random string (`python3 -c "import secrets; print(secrets.token_hex(32))"`) |
| `FLASK_ENV` | `production` |
| `APP_ENV` | `production` |
| `AZURE_SQL_CONNECTION_STRING` | Full ODBC connection string from `.env.example` |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` |

---

## Project structure

```
SmartSurplus/
├── app.py                  Main Flask application (routes, business logic)
├── db.py                   Database layer (SQLite + Azure SQL dual backend)
├── requirements.txt        Python dependencies (includes gunicorn for production)
├── gunicorn.conf.py        Gunicorn production server configuration
├── startup.sh              Azure App Service startup command
├── Procfile                Alternative platform startup (Heroku / Railway)
├── seed_demo.py            Demo data seeder
├── .env.example            Environment variable template
├── sql/
│   ├── schema_sqlite.sql   SQLite schema (auto-applied on first run)
│   └── schema_sqlserver.sql  Azure SQL schema (run via init-azure-db)
├── templates/              Jinja2 HTML templates
├── static/                 CSS, images
├── tests/                  Unittest suite (54 tests)
└── docs/
    ├── AZURE_DEPLOYMENT.md   Step-by-step Azure deployment guide
    ├── VIVA_GUIDE.md
    ├── BASELINE_TEST_CHECKLIST.md
    ├── prd.md
    └── srd.md
```

---

## Technologies

- **Python 3.11+** / **Flask 3.1**
- **SQLite** (local) / **Azure SQL Database** (production)
- **pyodbc** — ODBC bridge for Azure SQL
- **Gunicorn** — production WSGI server
- **Werkzeug** — password hashing, security utilities
- **HTML / CSS** — responsive, accessible UI

---

## Notes

- The old `instance/smartsurplus.db` is no longer used; the active schema lives in `instance/smartsurplus_v2.db`.
- Never commit `.env` or any file containing real passwords or secret keys.
- See `docs/AZURE_DEPLOYMENT.md` for the full list of items that require manual verification after first deployment on Azure.
