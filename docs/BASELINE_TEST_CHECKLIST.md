# SmartSurplus — Baseline Test Checklist (Phase 1)

Use this checklist to verify that the baseline application works cleanly before and after changes.

## Automated Baseline Tests

Run the isolated baseline test suite (uses a temporary SQLite directory; leaves `instance/smartsurplus_v2.db` untouched):

```bash
.venv/bin/python -m unittest discover -s tests -v
```

---

## Manual & Environment Verification Checklist

### 1. Environment & Startup
- [ ] Virtual environment active (`.venv`) with dependencies from `requirements.txt` installed (`pip check` passes).
- [ ] Application starts cleanly with `.venv/bin/python app.py` and serves `http://127.0.0.1:5000`.
- [ ] `static/css/style.css` returns HTTP `200` and applies styling without console errors.
- [ ] No real secrets, passwords, or `.env` files are tracked in Git.

### 2. Database Initialization & Schema
- [ ] `instance/smartsurplus_v2.db` initializes automatically via `db.init_db()`.
- [ ] All 5 tables (`users`, `categories`, `donations`, `requests`, `audit_log`), 4 custom indexes, 1 view (`v_open_donations`), and 6 default categories exist.
- [ ] `python seed_demo.py` inserts demo records once and prints `"Demo data already present."` on re-run.
- [ ] SQLite `PRAGMA foreign_keys = ON` is active on connections.

### 3. Authentication & Security
- [ ] `GET /register` creates `donor` or `recipient` accounts; rejects duplicate email (case-insensitive), invalid email, weak passwords (`< 8` or `> 128` chars, missing uppercase/lowercase/number/special character), and `role=admin` / invalid roles.
- [ ] `POST /login` rejects blank credentials, authenticates valid users, clears stale session data (`session.clear()`), sets session cookie (`HttpOnly`, `SameSite=Lax`), and resets failed attempt counter.
- [ ] 5 failed login attempts lock the account for 15 minutes (`ACCOUNT_LOCKED` & `LOGIN_LOCKED` audited); error message does not enumerate valid vs invalid emails; expired lock recovers automatically.
- [ ] `POST /logout` requires authentication, rejects `GET` (`405`), clears session securely, and redirects to `/`.
- [ ] `POST` requests without a valid `csrf_token` fail with `400 Bad Request`.
- [ ] Security headers (`Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`) are present on responses.

### 4. Role-Based Access Control (RBAC)
- [ ] Anonymous user visiting `/donate`, `/my`, or `/admin/audit` is redirected (`302`) to `/login`.
- [ ] `donor` can access `/donate` and `/my`; receives `403` on `/donations/<id>/claim` and `/admin/audit`.
- [ ] `recipient` can access `/donations`, `/donations/<id>/claim`, and `/my`; receives `403` on `/donate` and `/admin/audit`.
- [ ] `admin` can access `/admin/audit` and `/donate`; receives `403` on `/my` and `/donations/<id>/claim`.

### 5. Core Workflows
- [ ] **Create donation** (`/donate`): validates name, category, optional description (max 500), quantity (`1..100000`), location, and future deadline (including ISO timezone-aware values); inserts row with `remaining_quantity = quantity`.
- [ ] **Browse, search & filter** (`/donations`): lists only `'available'` listings with `remaining_quantity > 0` and future deadline (excluding expired, claimed, and cancelled); filters by keyword `q` (food name, location, description; `%` and `_` literal), `category`, `location`, `min_qty`, `urgency` (`6h`/`24h`), and `sort`; preserves form values, provides a clear-filters button, and safely handles malformed inputs.
- [ ] **View donation details** (`/donations/<id>`): displays all fields, status (`available`, `claimed`, `cancelled`, `expired`), remaining quantity, and owner/admin request summary.
- [ ] **Edit donation** (`/donations/<id>/edit`): owner donor or admin only; preserves claimed portions (`new_quantity >= claimed_qty`); prevents editing cancelled donations.
- [ ] **Cancel & delete donation** (`/donations/<id>/cancel`, `/donations/<id>/delete`): owner donor or admin only; cancelling cancels pending requests; deleting is blocked when requests exist.
- [ ] **Claim food & request lifecycle** (`POST /donations/<id>/claim`, `/requests/<id>/(accept|reject|complete|cancel)`): validates `1 <= qty <= 100000`, blocks self-claims, blocks expired/cancelled donations, prevents accidental double-POST via `claim_token`, atomically updates `remaining_quantity` and `status = 'claimed'` at `0` with full rollback on error, and restores stock on `reject`/`cancel`.
- [ ] **User dashboards** (`/my`): donor dashboard displays summary KPIs, remaining/total quantities, status filters, management actions, and incoming requests; receiver dashboard displays claims, pickup details, completed/cancelled/rejected metrics, and status actions; strict per-user isolation enforced.
- [ ] **Admin console & audit log** (`/admin`, `/admin/users`, `/admin/donations`, `/admin/requests`, `/admin/audit`): enforces `@login_required("admin")` (`403` for donors/recipients, `302` for anonymous); displays platform metrics, user search/suspension (never password hashes, blocks self-suspension), donation moderation, problematic request flags, and paginated/filtered audit log.
- [ ] **In-app notifications** (`/notifications`, `/notifications/<id>/read`, `/notifications/read-all`): persists notifications inside caller transactions (none created on failed DB rollback), tracks unread badge count, marks single/all notifications read, prevents duplicate expiry/approaching-deadline notifications via `dedup_key`, and enforces per-user ownership (`403` across users).

### 6. Error Pages & CLI
- [ ] `400`, `403`, `404`, `405`, `413`, and `500` render `templates/error.html` cleanly.
- [ ] `flask --app app make-admin <email>` promotes an existing user to `admin`.
- [ ] `flask --app app init-azure-db` fails gracefully with a clear message when `AZURE_SQL_CONNECTION_STRING` is unset.
