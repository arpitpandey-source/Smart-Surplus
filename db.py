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
    conn = sqlite3.connect(path)
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


def init_db():
    """Create tables. SQLite: automatic. Azure: use `flask init-azure-db`."""
    if not using_azure():
        run_script(os.path.join(BASE_DIR, "sql", "schema_sqlite.sql"))
