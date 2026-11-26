"""Database layer for SmartSurplus.

One small interface, two backends:
  * Azure SQL Database (via pyodbc)  - used when AZURE_SQL_CONNECTION_STRING is set
  * SQLite                           - used for quick local development

All queries use '?' placeholders (parameterised queries), which both
drivers support. This is what prevents SQL injection.
"""
import os
import re
import sqlite3

from flask import current_app, g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def using_azure():
    return bool(os.environ.get("AZURE_SQL_CONNECTION_STRING"))


def _connect():
    if using_azure():
        import pyodbc  # imported lazily so SQLite users don't need ODBC

        return pyodbc.connect(os.environ["AZURE_SQL_CONNECTION_STRING"], autocommit=False)

    os.makedirs(current_app.instance_path, exist_ok=True)
    path = os.path.join(current_app.instance_path, "smartsurplus_v2.db")
    conn = sqlite3.connect(path, timeout=15.0)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = _connect()
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def _rows(cursor):
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def query(sql, params=()):
    """Run a SELECT and return a list of dicts."""
    cur = get_db().cursor()
    cur.execute(sql, params)
    return _rows(cur)


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    """Run INSERT/UPDATE/DELETE (no commit). Returns affected row count."""
    cur = get_db().cursor()
    cur.execute(sql, params)
    return cur.rowcount


def insert(sql, params=()):
    """Run an INSERT (no commit) and return the new row's id."""
    cur = get_db().cursor()
    cur.execute(sql, params)
    if using_azure():
        cur.execute("SELECT CAST(SCOPE_IDENTITY() AS INT)")
        return cur.fetchone()[0]
    return cur.lastrowid


def commit():
    get_db().commit()


def rollback():
    get_db().rollback()


def paginate_sql(limit, offset):
    """Return dialect-specific pagination SQL and parameter tuple."""
    if using_azure():
        return "OFFSET ? ROWS FETCH NEXT ? ROWS ONLY", (offset, limit)
    return "LIMIT ? OFFSET ?", (limit, offset)


def run_script(path, conn=None):
    """Execute a .sql file; for Azure, batches are separated by GO lines."""
    own = conn is None
    conn = conn or _connect()
    with open(path, encoding="utf-8") as f:
        script = f.read()
    try:
        if using_azure():
            batches = [b.strip() for b in re.split(r"^\s*GO\s*$", script, flags=re.M | re.I)]
            cur = conn.cursor()
            for batch in batches:
                # skip batches that contain only comments
                code = re.sub(r"/\*.*?\*/", "", batch, flags=re.S)
                code = re.sub(r"--.*", "", code).strip()
                if code:
                    cur.execute(batch)
            conn.commit()
        else:
            conn.executescript(script)
            conn.commit()
    finally:
        if own:
            conn.close()


def _sqlite_columns(conn, object_name):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({object_name})").fetchall()}


def migrate_sqlite():
    """Apply non-destructive schema upgrades to an existing SQLite database."""
    conn = _connect()
    try:
        user_cols = _sqlite_columns(conn, "users")
        if "is_active" not in user_cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
            )

        donation_cols = _sqlite_columns(conn, "donations")
        if "description" not in donation_cols:
            conn.execute(
                "ALTER TABLE donations ADD COLUMN description TEXT NOT NULL DEFAULT ''"
            )

        view_cols = _sqlite_columns(conn, "v_open_donations")
        if "description" not in view_cols or "donor_id" not in view_cols:
            conn.execute("DROP VIEW IF EXISTS v_open_donations")
            conn.execute(
                "CREATE VIEW v_open_donations AS "
                "SELECT d.id, d.donor_id, d.food_name, d.description, d.quantity, "
                "d.remaining_quantity, d.location, d.deadline, d.created_at, "
                "c.name AS category, u.full_name AS donor_name "
                "FROM donations d "
                "JOIN categories c ON c.id = d.category_id "
                "JOIN users u      ON u.id = d.donor_id "
                "WHERE d.status = 'available' AND d.remaining_quantity > 0"
            )
        conn.commit()

        # ---- notifications table (added in Phase 9; migrate existing DBs) ----
        notif_cols = _sqlite_columns(conn, "notifications")
        if not notif_cols:
            conn.execute(
                """CREATE TABLE notifications (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL REFERENCES users(id),
                    donation_id INTEGER NULL     REFERENCES donations(id),
                    request_id  INTEGER NULL     REFERENCES requests(id),
                    event_type  TEXT    NOT NULL,
                    message     TEXT    NOT NULL,
                    is_read     INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0, 1)),
                    dedup_key   TEXT    NULL,
                    created_at  TEXT    NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_notifications_user_read "
                "ON notifications (user_id, is_read, id)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_notifications_user_dedup "
                "ON notifications (user_id, dedup_key) WHERE dedup_key IS NOT NULL"
            )
            conn.commit()

        req_ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'requests'"
        ).fetchone()
        if req_ddl and "'rejected'" not in req_ddl[0]:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.executescript(
                """
                BEGIN;
                CREATE TABLE requests_new (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    donation_id  INTEGER NOT NULL REFERENCES donations(id),
                    recipient_id INTEGER NOT NULL REFERENCES users(id),
                    quantity     INTEGER NOT NULL CHECK (quantity >= 1),
                    status       TEXT NOT NULL DEFAULT 'pending'
                                 CHECK (status IN ('pending', 'accepted', 'rejected', 'completed', 'cancelled')),
                    created_at   TEXT NOT NULL
                );
                INSERT INTO requests_new (id, donation_id, recipient_id, quantity, status, created_at)
                SELECT id, donation_id, recipient_id, quantity,
                       CASE WHEN status = 'collected' THEN 'completed' ELSE status END,
                       created_at
                FROM requests;
                DROP TABLE requests;
                ALTER TABLE requests_new RENAME TO requests;
                CREATE INDEX IF NOT EXISTS ix_requests_recipient ON requests (recipient_id);
                CREATE INDEX IF NOT EXISTS ix_requests_donation ON requests (donation_id);
                COMMIT;
                """
            )
            conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()


def init_db():
    """Create tables and run safe migrations. SQLite: automatic. Azure: `flask init-azure-db`."""
    if not using_azure():
        run_script(os.path.join(BASE_DIR, "sql", "schema_sqlite.sql"))
        migrate_sqlite()
