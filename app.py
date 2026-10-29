import os
import re
import secrets
from datetime import datetime, timedelta
from functools import wraps

import click
from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import db

try:  # optional: load variables from a local .env file
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# ---------------------------------------------------------------- security config
# Secrets come from the environment (in Azure: App settings / Key Vault), never from code.
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,      # JavaScript cannot read the cookie
    SESSION_COOKIE_SAMESITE="Lax",     # basic CSRF protection for cross-site requests
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV") == "production",  # HTTPS only in prod
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
    MAX_CONTENT_LENGTH=64 * 1024,      # reject huge request bodies
)
app.teardown_appcontext(db.close_db)

MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def parse_dt(value):
    """SQLite returns text, Azure SQL returns datetime; accept both."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


@app.template_filter("dt")
def fmt_dt(value):
    return parse_dt(value).strftime("%d %b %Y, %I:%M %p") if value else ""


# ---------------------------------------------------------------- CSRF + headers
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def load_user_and_check_csrf():
    g.user = None
    if "user_id" in session:
        g.user = db.query_one(
            "SELECT id, full_name, email, role FROM users WHERE id = ?",
            (session["user_id"],),
        )
        if g.user is None:
            session.clear()

    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Invalid or missing CSRF token.")


@app.after_request
def security_headers(resp):
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; "
        "frame-ancestors 'none'; form-action 'self'"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    if app.config["SESSION_COOKIE_SECURE"]:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


# ---------------------------------------------------------------- auth helpers
def login_required(*roles):
    """Decorator: require a logged-in user, optionally with one of the given roles (RBAC)."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                flash("Please log in to continue.", "error")
                return redirect(url_for("login"))
            if roles and g.user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def audit(action, detail=None, user_id=None):
    """Write a security/audit event (part of the same transaction as the caller)."""
    uid = user_id if user_id is not None else (g.user["id"] if g.get("user") else None)
    db.execute(
        "INSERT INTO audit_log (user_id, action, detail, ip_address, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, action, (detail or "")[:500], request.remote_addr, now_iso()),
    )


# ---------------------------------------------------------------- pages
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    error, form = None, {}
    if request.method == "POST":
        name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "")
        form = {"full_name": name, "email": email, "role": role}

        if not name or len(name) > 100:
            error = "Please enter your name (max 100 characters)."
        elif not EMAIL_RE.match(email) or len(email) > 255:
            error = "Please enter a valid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif role not in ("donor", "recipient"):   # 'admin' can never be self-selected
            error = "Please choose a role."
        elif db.query_one("SELECT id FROM users WHERE email = ?", (email,)):
            error = "An account with this email already exists."
        else:
            try:
                uid = db.insert(
                    "INSERT INTO users (full_name, email, password_hash, role, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (name, email, generate_password_hash(password), role, now_iso()),
                )
                audit("REGISTER", f"role={role}", user_id=uid)
                db.commit()
                flash("Account created. Please log in.", "success")
                return redirect(url_for("login"))
            except Exception:
                db.rollback()
                error = "Could not create the account. Please try again."
    return render_template("register.html", error=error, form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db.query_one("SELECT * FROM users WHERE email = ?", (email,))

        locked = user and user["locked_until"] and parse_dt(user["locked_until"]) > datetime.now()
        if locked:
            error = "Account temporarily locked after too many failed attempts. Try later."
        elif user and check_password_hash(user["password_hash"], password):
            db.execute("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = ?",
                       (user["id"],))
            audit("LOGIN_OK", user_id=user["id"])
            db.commit()
            session.clear()                      # new session on login (prevents fixation)
            session["user_id"] = user["id"]
            session.permanent = True
            return redirect(url_for("donations"))
        else:
            if user:
                attempts = user["failed_attempts"] + 1
                lock = None
                if attempts >= MAX_FAILED_LOGINS:
                    lock = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds")
                    attempts = 0
                db.execute("UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
                           (attempts, lock, user["id"]))
            audit("LOGIN_FAIL", f"email={email[:100]}", user_id=user["id"] if user else None)
            db.commit()
            error = "Invalid email or password."   # same message either way (no user enumeration)
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
@login_required()
def logout():
    audit("LOGOUT")
    db.commit()
    session.clear()
    return redirect(url_for("home"))


@app.route("/donate", methods=["GET", "POST"])
@login_required("donor", "admin")
def donate():
    error, form = None, {}
    categories = db.query("SELECT id, name FROM categories ORDER BY name")

    if request.method == "POST":
        food_name = request.form.get("food_name", "").strip()
        quantity_text = request.form.get("quantity", "").strip()
        location = request.form.get("location", "").strip()
        deadline_text = request.form.get("deadline", "").strip()
        category_text = request.form.get("category_id", "").strip()
        form = {"food_name": food_name, "quantity": quantity_text, "location": location,
                "deadline": deadline_text, "category_id": category_text}

        quantity = category_id = deadline = None
        if not food_name or not location or not deadline_text:
            error = "Please fill in all required fields."
        elif len(food_name) > 100 or len(location) > 200:
            error = "Food name or location is too long."
        else:
            try:
                quantity = int(quantity_text)
                category_id = int(category_text)
                if not 1 <= quantity <= 100000:
                    error = "Quantity must be between 1 and 100000."
            except ValueError:
                error = "Please enter a valid quantity and choose a category."
        if not error:
            try:
                deadline = datetime.fromisoformat(deadline_text)
                if deadline <= datetime.now():
                    error = "The collection deadline must be in the future."
            except ValueError:
                error = "Please enter a valid collection deadline."
        if not error and not any(c["id"] == category_id for c in categories):
            error = "Please choose a valid category."

        if not error:
            try:
                donation_id = db.insert(
                    "INSERT INTO donations (donor_id, category_id, food_name, quantity, "
                    "remaining_quantity, location, deadline, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'available', ?)",
                    (g.user["id"], category_id, food_name, quantity, quantity, location,
                     deadline.isoformat(timespec="seconds"), now_iso()),
                )
                audit("DONATION_CREATE", f"donation_id={donation_id}")
                db.commit()
                flash(f"Donation #{donation_id} saved successfully!", "success")
                return redirect(url_for("donations"))
            except Exception:
                db.rollback()
                error = "We couldn't save your donation. Please try again."
    return render_template("donate.html", error=error, form=form, categories=categories)


@app.route("/donations")
def donations():
    q = request.args.get("q", "").strip()[:100]
    category = request.args.get("category", "").strip()
    sql = ("SELECT * FROM v_open_donations WHERE deadline > ?")
    params = [now_iso()]
    if q:
        sql += " AND (food_name LIKE ? OR location LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY deadline"
    return render_template(
        "donations.html",
        donations=db.query(sql, params),
        categories=db.query("SELECT name FROM categories ORDER BY name"),
        q=q, selected=category,
    )


@app.route("/donations/<int:donation_id>/claim", methods=["POST"])
@login_required("recipient")
def claim(donation_id):
    """Reserve portions. One conditional UPDATE keeps this safe under concurrency:
    two recipients can never claim the same portions (ACID / no lost update)."""
    try:
        qty = int(request.form.get("quantity", ""))
    except ValueError:
        qty = 0
    if qty < 1:
        flash("Enter a valid number of portions.", "error")
        return redirect(url_for("donations"))

    try:
        changed = db.execute(
            "UPDATE donations SET remaining_quantity = remaining_quantity - ? "
            "WHERE id = ? AND status = 'available' AND remaining_quantity >= ? AND deadline > ?",
            (qty, donation_id, qty, now_iso()),
        )
        if changed != 1:
            db.rollback()
            flash("Sorry, that many portions are no longer available.", "error")
            return redirect(url_for("donations"))

        db.execute("UPDATE donations SET status = 'claimed' "
                   "WHERE id = ? AND remaining_quantity = 0", (donation_id,))
        req_id = db.insert(
            "INSERT INTO requests (donation_id, recipient_id, quantity, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (donation_id, g.user["id"], qty, now_iso()),
        )
        audit("CLAIM", f"donation_id={donation_id} qty={qty} request_id={req_id}")
        db.commit()                       # all three statements succeed together...
        flash(f"Reserved {qty} portion(s). Request #{req_id} created.", "success")
    except Exception:
        db.rollback()                     # ...or none of them do (atomicity)
        flash("Could not complete your request. Please try again.", "error")
    return redirect(url_for("my_activity"))


@app.route("/my")
@login_required("donor", "recipient")
def my_activity():
    if g.user["role"] == "recipient":
        rows = db.query(
            "SELECT r.id, r.quantity, r.status, r.created_at, d.food_name, d.location, d.deadline "
            "FROM requests r JOIN donations d ON d.id = r.donation_id "
            "WHERE r.recipient_id = ? ORDER BY r.id DESC", (g.user["id"],))
    else:
        rows = db.query(
            "SELECT d.id, d.food_name, d.quantity, d.remaining_quantity, d.deadline, d.status, "
            "COUNT(r.id) AS request_count "
            "FROM donations d LEFT JOIN requests r ON r.donation_id = d.id "
            "WHERE d.donor_id = ? "
            "GROUP BY d.id, d.food_name, d.quantity, d.remaining_quantity, d.deadline, d.status "
            "ORDER BY d.id DESC", (g.user["id"],))
    return render_template("my_activity.html", rows=rows)


@app.route("/admin/audit")
@login_required("admin")
def audit_view():
    rows = db.query(
        "SELECT a.id, a.action, a.detail, a.ip_address, a.created_at, u.email "
        "FROM audit_log a LEFT JOIN users u ON u.id = a.user_id ORDER BY a.id DESC")
    return render_template("audit.html", rows=rows[:200])


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
def http_error(err):
    return render_template("error.html", err=err), err.code


# ---------------------------------------------------------------- CLI
@app.cli.command("init-azure-db")
def init_azure_db():
    """Create tables/view/seed data in Azure SQL Database."""
    if not db.using_azure():
        raise click.ClickException("Set AZURE_SQL_CONNECTION_STRING first.")
    db.run_script(os.path.join(db.BASE_DIR, "sql", "schema_sqlserver.sql"))
    click.echo("Azure SQL schema ready.")


@app.cli.command("make-admin")
@click.argument("email")
def make_admin(email):
    """Promote an existing user to admin."""
    with app.app_context():
        n = db.execute("UPDATE users SET role = 'admin' WHERE email = ?", (email.lower(),))
        db.commit()
    click.echo("Done." if n else "No such user.")


with app.app_context():
    db.init_db()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_ENV") != "production")
