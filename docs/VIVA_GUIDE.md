# SmartSurplus – Viva Guide
Subjects: **Azure Data (DP-900) · Database Management Systems · System Design · Cloud Security** (Project-Based Learning)

How to use: for each topic → *what it is* → *where it is in the project* → *likely viva questions with short model answers*. Open the named file when the teacher asks "show me".

---
## 0. One-minute project pitch
SmartSurplus is a web app that connects food donors (restaurants, bakeries) with recipients (NGOs, individuals). Donors list surplus food; recipients reserve portions before a deadline. Built with **Flask (Python)**; data stored in **Azure SQL Database** (PaaS relational DB); locally it falls back to SQLite so it can be demoed offline.

| Layer | Technology | File |
|---|---|---|
| Presentation | HTML/CSS (Jinja templates) | `templates/`, `static/css/style.css` |
| Application | Flask routes, validation, auth, RBAC | `app.py` |
| Data access | Parameterised SQL, 2 backends | `db.py` |
| Database | Azure SQL (T-SQL) / SQLite | `sql/schema_sqlserver.sql`, `sql/schema_sqlite.sql` |

---
## 1. Azure Data Fundamentals (DP-900)

### Concepts to know
- **Data types**: structured (tables), semi-structured (JSON), unstructured (images, video).
- **Relational vs non-relational**: relational = tables, schema, SQL, ACID; non-relational = key-value, document (Cosmos DB), column-family, graph.
- **Azure SQL family**: *SQL Server on VM* (IaaS), *Azure SQL Managed Instance* (PaaS, near-100% SQL Server compatibility), **Azure SQL Database** (PaaS, single DB / elastic pool) ← used here.
- **Other Azure data services**: Cosmos DB (NoSQL), Blob Storage (unstructured), Azure Synapse / Data Factory / Databricks (analytics), Power BI (visualisation), Azure Database for PostgreSQL/MySQL.
- **Roles**: Database Administrator (secure, back up, tune), Data Engineer (pipelines), Data Analyst (reports).
- **Workloads**: **OLTP** (many small transactions – our app) vs **OLAP** (analytics on large history – Synapse/Power BI). Batch vs streaming.
- **IaaS / PaaS / SaaS** and the **shared responsibility model**.

### In this project
- `AZURE_SQL_CONNECTION_STRING` makes `db.py` connect to Azure SQL through **pyodbc + ODBC Driver 18**.
- `flask --app app init-azure-db` runs `sql/schema_sqlserver.sql` on Azure.
- Workload type: **OLTP** (insert donation, reserve portions).

### Likely questions
1. **Why Azure SQL Database and not Cosmos DB?** Data is structured with clear relationships (users–donations–requests), needs ACID (two people must not claim the same portions), joins and constraints → relational. Cosmos DB suits flexible schema/global scale/low-latency key lookups.
2. **What does PaaS mean here?** Microsoft manages OS, patching, backups, HA; we manage only schema, data, access and security.
3. **Azure SQL Database vs Managed Instance vs SQL on VM?** Database = single managed DB, cheapest, least admin; MI = instance-level features (SQL Agent, cross-DB queries) for lift-and-shift; VM = full OS control (IaaS) but we patch it.
4. **Built-in Azure SQL features?** Automatic backups with point-in-time restore, geo-replication, high availability SLA, TDE encryption at rest, Microsoft Defender for SQL, auditing, serverless auto-pause tier.
5. **OLTP vs OLAP – which is this?** OLTP. If we wanted "food saved per month" dashboards over years of data we'd feed Synapse/Power BI (OLAP).
6. **Where would images of food go?** Azure Blob Storage (unstructured); store only the blob URL in SQL.
7. **What is a DTU / vCore?** Two purchasing models: DTU = bundled CPU+memory+IO; vCore = choose CPU/memory/storage separately. Serverless vCore auto-pauses (good for a student project).
8. **What is a connection string and where is it kept?** Server, DB, credentials, `Encrypt=yes`. Kept in an environment variable/`.env` (git-ignored); in production in App Service settings / Key Vault – never in code.

### Azure setup steps (for your demo)
1. Portal → *Create a resource → SQL Database* → new **logical server** (SQL authentication, note admin login), choose **Serverless / Basic** tier.
2. Server → *Networking* → add **your client IP** to the firewall (and keep "Allow Azure services" for later App Service).
3. Open DB → *Query editor* (or Azure Data Studio/SSMS) and run `sql/schema_sqlserver.sql` – or set the env var and run `flask --app app init-azure-db`.
4. Create least-privilege user `smartsurplus_app` (commented block at the bottom of the schema file) and use *its* credentials in the app.
5. Install **ODBC Driver 18 for SQL Server**, `pip install -r requirements.txt`, set `.env`, run `python app.py`.

---
## 2. Database Management Systems

### Schema (ER summary)
```
users (1) ────< donations (1) ────< requests >──── (1) users
                   │ >──(1) categories
users (0..1) ────< audit_log
```
| Table | Key columns | Notes |
|---|---|---|
| `users` | PK id; UNIQUE email; role CHECK | password stored as hash; lockout columns |
| `categories` | PK id; UNIQUE name | lookup table (removes repeated text) |
| `donations` | PK id; FK donor_id→users; FK category_id→categories | CHECK quantity 1..100000, remaining ≤ quantity |
| `requests` | PK id; FK donation_id; FK recipient_id | resolves **many-to-many** between recipients and donations |
| `audit_log` | PK id; FK user_id (nullable) | append-only security trail |
Object extras: **view** `v_open_donations` (JOIN of 3 tables), **indexes** on `(status, deadline)`, `donor_id`, `recipient_id`, `donation_id`.

### Concepts → where to point
| Concept | Where |
|---|---|
| Primary / foreign / unique / check / default constraints | `sql/schema_*.sql` |
| **Normalization to 3NF** | category names moved to `categories`; donor details only in `users`; no repeating groups |
| One-to-many, many-to-many (via `requests`) | ER above |
| **Joins** (INNER, LEFT) | `v_open_donations`; `my_activity()` LEFT JOIN + `COUNT` + `GROUP BY` |
| **Aggregation** | `COUNT(r.id) ... GROUP BY` in `my_activity()` |
| **View** | `v_open_donations` (hides join complexity; can restrict columns for security) |
| **Index** | `ix_donations_status_deadline` speeds the main listing query (`WHERE status… AND deadline…`) |
| **Transactions / ACID** | `claim()` in `app.py`: UPDATE stock + INSERT request + INSERT audit → `commit()` or `rollback()` |
| **Concurrency control** | conditional `UPDATE … WHERE remaining_quantity >= ?` is atomic – no lost update / overselling |
| **SQL injection defence** | every query uses `?` placeholders |
| **DML / DDL / DCL** | INSERT-UPDATE-SELECT in `app.py`; CREATE in schema; GRANT in the schema's last block |

### Likely questions
1. **Why is the DB in 3NF?** 1NF: atomic values; 2NF: no partial dependency (all PKs are single-column); 3NF: no transitive dependency (category name depends on category, not on donation; donor name depends on user, not donation).
2. **Original design vs now?** Original had one flat `donations` table (food, location only). Now: users, categories, requests, audit – supports roles, ownership, reservations, accountability.
3. **Why a `requests` table?** One donation can be claimed by many recipients (in portions) and a recipient can claim many donations → many-to-many resolved by an associative table.
4. **What is ACID?** Atomicity (all or nothing – rollback on error), Consistency (CHECK/FK rules always hold), Isolation (concurrent claims don't interfere), Durability (committed data survives crashes).
5. **What happens if two recipients claim the last 5 portions at the same moment?** The `UPDATE … WHERE remaining_quantity >= 5` is executed atomically by the engine; the first succeeds (row count 1), the second matches 0 rows → we roll back and show "no longer available". Without that, a read-then-write would cause a lost update/race condition.
6. **Difference between DELETE, TRUNCATE, DROP?** DELETE removes rows (logged, can filter, can roll back); TRUNCATE removes all rows fast, resets identity; DROP removes the object. The app user gets **none** of these.
7. **WHERE vs HAVING?** WHERE filters rows before grouping; HAVING filters groups after aggregation.
8. **Clustered vs non-clustered index?** Clustered = physical row order (one per table; the PK by default); non-clustered = separate structure with pointers (our `ix_*`). Trade-off: faster reads, slower writes, more storage.
9. **What is a view and why use it?** A saved SELECT: simplifies queries, can expose only some columns/rows (security), no data stored (unless indexed/materialised).
10. **IDENTITY vs AUTOINCREMENT?** Same idea (surrogate key), T-SQL uses `IDENTITY(1,1)`, SQLite `AUTOINCREMENT`; we fetch the new id with `SCOPE_IDENTITY()` / `lastrowid` in `db.insert()`.
11. **Inner vs left join?** Inner = only matching rows; left = all left rows, NULLs when no match (donor with zero requests still appears).
12. **Candidate key / super key / surrogate key?** `email` is a candidate key (unique); `id` is the chosen surrogate primary key.
13. **Why `DATETIME2` not `VARCHAR` for dates in Azure?** Correct type → comparisons, range indexes, date functions, validation.
14. **What is a stored procedure / trigger?** Not used; could move `claim` logic into `sp_claim_donation` (benefit: one round trip, permission via EXECUTE only). Be honest: we kept logic in the app for portability across SQLite/Azure.

### Handy queries to demo live (Azure Query editor)
```sql
SELECT * FROM v_open_donations;
SELECT u.full_name, COUNT(*) AS listings, SUM(d.quantity) AS portions
FROM donations d JOIN users u ON u.id = d.donor_id GROUP BY u.full_name;
SELECT action, COUNT(*) FROM audit_log GROUP BY action;
```

---
## 3. System Design

### Architecture
```
 Browser ──HTTPS──▶ Flask app (local / Azure App Service) ──TLS 1433──▶ Azure SQL Database
   │                  routes · validation · auth · RBAC · CSRF           tables · constraints · view · indexes
   └─ HTML/CSS         audit logging · security headers                  backups · TDE · firewall · auditing
```
Pattern: **3-tier / layered (MVC-like)** – templates (view), routes (controller), `db.py` + SQL (model/data).

### Functional & non-functional requirements
- **Functional**: register/login, donors list food, recipients search & reserve, donors see requests, admin audit log.
- **Non-functional**: *security* (hashing, RBAC, CSRF), *integrity/consistency* (constraints, transactions), *availability* (Azure SLA, backups), *scalability* (stateless app, scale up DB tier / add app instances), *maintainability* (layers, one DB interface), *portability* (SQLite ↔ Azure).

### Key design decisions (be ready to justify)
| Decision | Why | Trade-off |
|---|---|---|
| Relational DB | structured, relational, needs ACID | less flexible schema than NoSQL |
| Stateless app, session in signed cookie | easy horizontal scaling | cookie size limited; secret key must be shared across instances |
| DB abstraction (`db.py`) | demo offline; same SQL | restricts to common SQL subset |
| Conditional UPDATE for claim | atomic, no explicit locks | slightly less readable than stored proc |
| Audit table | accountability & forensics | table growth → archive/partition |
| Pessimistic vs optimistic | we used optimistic style (check in the WHERE) | retry/failed message on conflict |

### Likely questions
1. **Draw the architecture and trace "reserve food".** Browser POST `/donations/<id>/claim` + CSRF token → Flask checks session & role → validates qty → single transaction on Azure SQL (update stock, insert request, insert audit) → commit → redirect.
2. **How would it scale to 1M users?** Stateless app → multiple App Service instances behind a load balancer/Front Door; scale SQL tier or add read replicas; cache listing queries (Azure Cache for Redis); paginate; CDN for static/images in Blob; queue (Service Bus) for notifications.
3. **Single points of failure?** One DB → use zone-redundant tier/geo-replication; one app instance → scale out.
4. **Vertical vs horizontal scaling?** Vertical = bigger machine/tier; horizontal = more instances. App scales horizontally (stateless); DB mostly vertically (or read replicas/sharding).
5. **CAP theorem?** Azure SQL favours consistency (CP-ish within a region); Cosmos DB lets you tune consistency levels.
6. **Why no pagination yet?** Honest limitation; add `OFFSET … FETCH` (T-SQL) / `LIMIT` in SQLite.
7. **Future features?** Email/SMS alerts (Logic Apps/Communication Services), geo-search, food photos in Blob, expiry job (Azure Functions timer), Power BI impact dashboard, CI/CD with GitHub Actions.
8. **Why stateless?** Any instance can serve any request; no sticky sessions.
9. **Caching, load balancing, CDN – used?** Not yet; describe where they'd go (see Q2).
10. **Monolith vs microservices?** Monolith is right for a small team/early stage; split later (auth, listings, notifications) only when needed.

---
## 4. Cloud Security

### Controls implemented (with proof)
| Threat / goal | Control | Where |
|---|---|---|
| Password theft | **Salted hash** (`werkzeug` scrypt/pbkdf2) – never plain text | `register()`, `login()` |
| Brute force | **Lockout** after 5 failures for 15 min + audit | `login()` |
| User enumeration | Same "Invalid email or password" message | `login()` |
| Session hijack/fixation | HttpOnly, SameSite=Lax, Secure (prod), 2h lifetime, `session.clear()` on login | `app.config`, `login()` |
| **CSRF** | per-session token checked on every POST | `load_user_and_check_csrf()` |
| **SQL injection** | parameterised queries only | `db.py`, all queries |
| **XSS** | Jinja auto-escaping + **Content-Security-Policy** (no inline JS/CSS) | templates, `security_headers()` |
| Clickjacking | `X-Frame-Options: DENY`, `frame-ancestors 'none'` | `security_headers()` |
| Privilege escalation | **RBAC** decorator; role `admin` cannot be chosen at signup; `make-admin` CLI | `login_required()`, `register()` |
| Input abuse | server-side validation, length limits, `MAX_CONTENT_LENGTH` | routes |
| Secrets in code | env vars / `.env` (git-ignored), `.env.example` | `app.py`, `.gitignore` |
| Accountability | `audit_log` for register/login/fail/donate/claim | `audit()` |
| DB blast radius | **Least-privilege** DB user (no DELETE/ALTER/DROP) | schema file bottom |
| Data in transit | `Encrypt=yes` to Azure SQL (TLS); HTTPS + HSTS in production | connection string, headers |

### Azure-side security to explain (configure in portal)
- **Azure SQL firewall** (server-level IP rules) + disable public access / **Private Endpoint** in production.
- **Encryption at rest – TDE** (on by default); **in transit – TLS 1.2**; Always Encrypted / Dynamic Data Masking for sensitive columns.
- **Authentication**: SQL auth (used) vs **Microsoft Entra ID** (preferred) and **Managed Identity** for App Service → no password at all.
- **Azure Key Vault** for secrets; **RBAC (Azure roles)** for who manages resources; **Microsoft Defender for SQL** (threat detection, vulnerability assessment); **Auditing** to Log Analytics; **Backups** with point-in-time restore.
- **Shared responsibility**: Microsoft secures datacentre, hardware, DB engine patching; we secure identities, data, app code, network rules.

### Likely questions
1. **Authentication vs authorization?** Who you are (login) vs what you may do (role checks → 403).
2. **Why hash, not encrypt, passwords?** Hash is one-way; even DB admins can't recover them. Salt defeats rainbow tables; slow hash (scrypt) resists brute force.
3. **Explain SQL injection with our code.** `"…WHERE email = '" + email + "'"` lets `' OR 1=1 --` change the query. We pass `?` params so input is always data. Test: search `' OR 1=1 --` → treated as plain text.
4. **Explain CSRF.** A malicious site makes your logged-in browser POST to us. We require a secret token tied to the session that the attacker can't read; plus SameSite cookies.
5. **XSS and our defence?** Injecting script via input (e.g., food name `<script>`). Jinja escapes output; CSP blocks inline scripts. (Test: food name `<b>x</b>` shows literally.)
6. **Principle of least privilege?** App DB user has only SELECT/INSERT/UPDATE on needed tables; users have roles; admin separate.
7. **CIA triad mapping?** Confidentiality – hashing, RBAC, TLS, firewall; Integrity – constraints, transactions, audit; Availability – Azure SLA, backups, lockout/limits.
8. **Defence in depth?** Multiple layers: network (firewall), identity (auth/RBAC), app (validation/CSRF/CSP), data (constraints, TDE), monitoring (audit/Defender).
9. **What is a Managed Identity?** Azure-managed credential for an app to access Azure SQL/Key Vault without storing a password.
10. **How do you handle a leaked secret?** Rotate immediately (SQL password, `SECRET_KEY`), purge from git history, review audit logs; use Key Vault going forward.
11. **Known limitations (say them first – examiners respect honesty)**: SQL auth instead of Entra/Managed Identity; no rate-limiting per IP/CAPTCHA; no email verification or MFA; no HTTPS locally; audit log not tamper-proof; no pagination; connection per request without pooling config.

---
## 5. Likely "show me" demo script (3 min)
1. Azure portal → SQL server → Networking (firewall) → SQL database → Query editor: `SELECT * FROM v_open_donations`.
2. Run app → register donor + recipient → donor lists food (show XSS text is escaped).
3. Recipient reserves portions; try reserving more than left → error (transaction/atomicity).
4. Try `' OR 1=1 --` in search; show it returns nothing (injection-safe).
5. Wrong password 5× → locked; admin page `/admin/audit` shows the events.
6. Show `schema_sqlserver.sql` constraints and the GRANT block (least privilege).

## 6. Quick glossary
ACID · PK/FK · 1NF–3NF · OLTP/OLAP · IaaS/PaaS/SaaS · DTU/vCore · TDE · TLS · RBAC · CSRF · XSS · CSP · SQL injection · least privilege · defence in depth · managed identity · Key Vault · shared responsibility.
