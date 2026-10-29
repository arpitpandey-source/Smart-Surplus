
# SmartSurplus 🌱

An intelligent food waste reduction and redistribution platform
that aims to connect surplus food donors with eligible recipients.

## Problem Statement

Food that could be redistributed is sometimes wasted because
donors and potential recipients lack an easy way to connect.

SmartSurplus aims to make surplus food easier to discover,
request, and redistribute responsibly.

## Current Features

- Responsive homepage
- Food donation form
- Server-side form validation
- Collection deadline validation
- Temporary donation listing preview

## Technologies Used

- Python
- Flask
- HTML
- CSS
- Git and GitHub

## Getting Started

### 1. Clone the repository

```bash
git clone YOUR_GITHUB_REPOSITORY_URL
cd SmartSurplus
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Run the application

```bash
python app.py
```

Open http://127.0.0.1:5000 in your browser.

## Development Status

This project is under active development.
Features and documentation will be updated as development progresses.

## Learning Goals

- Learn Flask web development
- Build and validate HTML forms
- Integrate a database
- Implement food listing and claiming workflows
- Explore intelligent food matching
- Practise Git and GitHub workflows

## Accounts, Azure SQL and security (PBL upgrade)

- Roles: donor, recipient, admin. Login with hashed passwords, lockout, CSRF, CSP headers, audit log.
- Database: local SQLite by default; set `AZURE_SQL_CONNECTION_STRING` (see `.env.example`) to use Azure SQL Database.
  First time on Azure: `flask --app app init-azure-db`
- Requires the *ODBC Driver 18 for SQL Server* when using Azure.
- Promote an admin: `flask --app app make-admin you@example.com`
- Viva preparation: see `docs/VIVA_GUIDE.md`

Note: the old `instance/smartsurplus.db` is no longer used; the new schema lives in `instance/smartsurplus_v2.db`.
