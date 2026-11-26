# SmartSurplus — Azure Deployment Guide

> **Who this is for:** Students and beginners deploying SmartSurplus on Microsoft Azure for the first time.
> No prior Azure experience is required.

---

## Table of Contents

1. [Cost overview](#cost-overview)
2. [Prerequisites](#prerequisites)
3. [Step 1 — Create an Azure account](#step-1--create-an-azure-account)
4. [Step 2 — Create the Azure SQL Database](#step-2--create-the-azure-sql-database)
5. [Step 3 — Create the App Service](#step-3--create-the-app-service)
6. [Step 4 — Configure environment variables](#step-4--configure-environment-variables)
7. [Step 5 — Initialise the database schema](#step-5--initialise-the-database-schema)
8. [Step 6 — Deploy the code](#step-6--deploy-the-code)
9. [Step 7 — Verify the deployment](#step-7--verify-the-deployment)
10. [Environment variable reference](#environment-variable-reference)
11. [Viewing logs](#viewing-logs)
12. [Troubleshooting](#troubleshooting)
13. [What is NOT verified without credentials](#what-is-not-verified-without-credentials)

---

## Cost overview

| Resource | Tier | Approximate monthly cost |
|---|---|---|
| Azure App Service | **F1 Free** | \$0 |
| Azure SQL Database | **Basic (5 DTU)** | ~\$5 USD |
| Total | | ~\$5 USD/month |

> [!TIP]
> New Azure accounts get a **\$200 free credit** valid for 30 days, which covers everything here.
> Azure for Students also offers free credits without a credit card.

---

## Prerequisites

Before you start, install these tools on your computer:

| Tool | Why needed | Install link |
|---|---|---|
| Python 3.11+ | Runs the app | https://python.org |
| Git | Pushes code to Azure | https://git-scm.com |
| Azure CLI | Creates Azure resources | https://aka.ms/installazurecliwindows |
| ODBC Driver 18 for SQL Server | Connects Python to Azure SQL | https://aka.ms/odbc18 |

After installing the Azure CLI, sign in:
```bash
az login
```

---

## Step 1 — Create an Azure account

1. Go to https://azure.microsoft.com/free
2. Click **Start free** and create a Microsoft account (or use an existing one).
3. Fill in your details. A credit card is required for identity verification, but you will **not** be charged during the free trial.

---

## Step 2 — Create the Azure SQL Database

### 2a. Create a Resource Group

A resource group keeps all your Azure resources together.

```bash
az group create --name smartsurplus-rg --location eastus
```

### 2b. Create an Azure SQL Server

Replace `<your-server-name>` with something unique (e.g. `smartsurplus-arpit`).
Replace `<AdminPassword>` with a strong password (minimum 8 characters, uppercase, lowercase, digit, symbol).

```bash
az sql server create \
  --name <your-server-name> \
  --resource-group smartsurplus-rg \
  --location eastus \
  --admin-user smartsurplus_admin \
  --admin-password "<AdminPassword>"
```

### 2c. Allow your IP address through the firewall

```bash
az sql server firewall-rule create \
  --resource-group smartsurplus-rg \
  --server <your-server-name> \
  --name AllowMyIP \
  --start-ip-address 0.0.0.0 \
  --end-ip-address 0.0.0.0
```

> [!NOTE]
> `0.0.0.0` to `0.0.0.0` is a special Azure shorthand that allows **all Azure services** (including App Service) to reach the SQL server.
> For tighter security in production, restrict this to specific IP ranges.

### 2d. Create the database

```bash
az sql db create \
  --resource-group smartsurplus-rg \
  --server <your-server-name> \
  --name smartsurplus \
  --edition Basic \
  --capacity 5
```

### 2e. Create the application-level login (least-privilege)

Connect to the master database and run:

```sql
-- In master database
CREATE LOGIN smartsurplus_app WITH PASSWORD = '<AppPassword>';
```

Then connect to the `smartsurplus` database and run the grant section at the bottom of `sql/schema_sqlserver.sql`:

```sql
-- In smartsurplus database
CREATE USER smartsurplus_app FOR LOGIN smartsurplus_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA::dbo TO smartsurplus_app;
-- (the full grants are at the bottom of sql/schema_sqlserver.sql)
```

You can connect to Azure SQL using [Azure Data Studio](https://aka.ms/azuredatastudio) (free) or the Azure portal Query Editor.

---

## Step 3 — Create the App Service

### 3a. Create an App Service Plan (Free tier)

```bash
az appservice plan create \
  --name smartsurplus-plan \
  --resource-group smartsurplus-rg \
  --sku F1 \
  --is-linux
```

### 3b. Create the Web App

Replace `<your-app-name>` with a globally unique name (e.g. `smartsurplus-arpit`).

```bash
az webapp create \
  --resource-group smartsurplus-rg \
  --plan smartsurplus-plan \
  --name <your-app-name> \
  --runtime "PYTHON:3.11"
```

### 3c. Set the startup command

```bash
az webapp config set \
  --resource-group smartsurplus-rg \
  --name <your-app-name> \
  --startup-file startup.sh
```

---

## Step 4 — Configure environment variables

App settings in Azure App Service are equivalent to environment variables.
They are **encrypted at rest** and never stored in your code.

### Build the connection string

Your connection string looks like this (all on one line):

```
Driver={ODBC Driver 18 for SQL Server};Server=tcp:<your-server-name>.database.windows.net,1433;Database=smartsurplus;Uid=smartsurplus_app;Pwd=<AppPassword>;Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;
```

### Set all required settings

```bash
az webapp config appsettings set \
  --resource-group smartsurplus-rg \
  --name <your-app-name> \
  --settings \
    SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')" \
    FLASK_ENV="production" \
    APP_ENV="production" \
    AZURE_SQL_CONNECTION_STRING="Driver={ODBC Driver 18 for SQL Server};Server=tcp:<your-server-name>.database.windows.net,1433;Database=smartsurplus;Uid=smartsurplus_app;Pwd=<AppPassword>;Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;" \
    SCM_DO_BUILD_DURING_DEPLOYMENT="true"
```

> [!CAUTION]
> Never put your real password or SECRET_KEY in a file that gets committed to Git.
> Always set secrets through Azure App Settings or Azure Key Vault.

---

## Step 5 — Initialise the database schema

This is a **one-time manual step**. Do it before the first deployment, or whenever you reset the database.

> [!IMPORTANT]
> Run this locally from your project folder, **not** on the server.
> Your local machine must be able to reach Azure SQL (firewall rule from Step 2c).

```bash
# In your project folder with .venv activated:
cp .env.example .env
# Edit .env: set AZURE_SQL_CONNECTION_STRING and APP_ENV=production

source .venv/bin/activate        # Mac/Linux
# .venv\Scripts\activate         # Windows

flask --app app init-azure-db
```

Expected output:
```
Azure SQL schema ready.
```

After this, run the seed script if you want demo data:
```bash
python seed_demo.py
```

---

## Step 6 — Deploy the code

### Option A — Local Git deployment (simplest)

```bash
# Configure local Git deployment
az webapp deployment source config-local-git \
  --resource-group smartsurplus-rg \
  --name <your-app-name>
```

This prints a Git URL like `https://<your-app-name>.scm.azurewebsites.net/<your-app-name>.git`.

```bash
# Add Azure as a remote and push
git remote add azure https://<your-app-name>.scm.azurewebsites.net/<your-app-name>.git
git push azure main
```

Azure will automatically run `pip install -r requirements.txt` during deployment.

### Option B — GitHub Actions (recommended for ongoing development)

1. Fork or push your code to a GitHub repository.
2. In Azure portal → Your App Service → Deployment Center → GitHub → Authorize → Select your repo and branch → Save.
3. Azure generates a workflow file (`.github/workflows/main_<app-name>.yml`) automatically.
4. Every `git push` to your main branch will deploy automatically.

---

## Step 7 — Verify the deployment

```bash
# Open the app in your browser
az webapp browse --resource-group smartsurplus-rg --name <your-app-name>
```

You should see the SmartSurplus home page. Try:
- Registering an account
- Creating a donation
- Claiming a donation

---

## Environment variable reference

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | **Yes** | Long random string for session signing. Generate with `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `FLASK_ENV` | Yes | Set to `production` on Azure |
| `APP_ENV` | Yes | Set to `production` on Azure |
| `AZURE_SQL_CONNECTION_STRING` | Yes (Azure) | Full ODBC connection string. Leave blank to use local SQLite |
| `NOTIFY_DEADLINE_WINDOW_HOURS` | No | Hours before deadline to send approaching-deadline notifications (default: 24) |

---

## Viewing logs

Stream live logs from Azure:

```bash
az webapp log tail \
  --resource-group smartsurplus-rg \
  --name <your-app-name>
```

Or view them in the Azure portal: Your App Service → Monitoring → Log stream.

Enable persistent logging (highly recommended):

```bash
az webapp log config \
  --resource-group smartsurplus-rg \
  --name <your-app-name> \
  --application-logging filesystem \
  --level information
```

---

## Troubleshooting

### "No module named pyodbc"

The ODBC driver is not installed. Download and install ODBC Driver 18 for SQL Server:
https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server

### "Login failed for user" / connection errors

- Double-check the `Uid` and `Pwd` in your connection string.
- Make sure the firewall rule (Step 2c) is in place.
- Confirm the database name in the connection string matches the database you created.

### "SSL connection is required"

Add `Encrypt=yes` to your connection string (it should already be there in the template).

### "No space left on device" on Free tier

The F1 Free tier has 1 GB storage. If you hit this, upgrade to the B1 Basic plan (~\$13/month).

### App shows a 500 error immediately after deployment

1. Check the logs: `az webapp log tail ...`
2. Confirm all App Settings are saved correctly.
3. Confirm `flask init-azure-db` was run successfully before deployment.

---

## What is NOT verified without credentials

> [!WARNING]
> The following items have been designed and implemented based on Azure SQL documentation,
> but could **not** be tested end-to-end because no Azure credentials were available during development.
> Please verify each item manually after your first deployment.

- [ ] Azure SQL connection string accepted by pyodbc with ODBC Driver 18
- [ ] `flask --app app init-azure-db` creates all tables, indexes, views, and grants correctly
- [ ] `SCOPE_IDENTITY()` returns the correct last-inserted ID for all INSERT operations
- [ ] `OFFSET ? ROWS FETCH NEXT ? ROWS ONLY` pagination works correctly in admin views
- [ ] `BIT` columns (`is_active`, `is_read`) are truthy/falsy in Jinja templates as expected
- [ ] `DATETIME2(0)` columns accept ISO-8601 string parameters from pyodbc without conversion errors
- [ ] Filtered unique index on `notifications(user_id, dedup_key) WHERE dedup_key IS NOT NULL` is enforced
- [ ] `v_open_donations` view is queryable and returns correct rows
- [ ] User suspension (`is_active = 0`) blocks login correctly
- [ ] All 54 automated tests pass when `AZURE_SQL_CONNECTION_STRING` is set (tests currently use SQLite)

---

*This guide was written for SmartSurplus Phase 11 (Azure App Service deployment).*
*All 54 automated tests pass on SQLite. Azure SQL compatibility has been reviewed against official documentation.*

