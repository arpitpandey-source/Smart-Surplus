import logging
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

# ---------------------------------------------------------------- logging
# In production (Gunicorn + Azure App Service) logs go to stdout/stderr so
# Azure Log Stream and 'az webapp log tail' can pick them up automatically.
if not app.debug:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
logger = logging.getLogger(__name__)

def is_production():
    return (
        os.environ.get("FLASK_ENV") == "production"
        or os.environ.get("APP_ENV") == "production"
    )


# ---------------------------------------------------------------- security config
# Secrets come from the environment (in Azure: App settings / Key Vault), never from code.
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,      # JavaScript cannot read the cookie
    SESSION_COOKIE_SAMESITE="Lax",     # basic CSRF protection for cross-site requests
    SESSION_COOKIE_SECURE=is_production(),  # HTTPS only in prod
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
    MAX_CONTENT_LENGTH=64 * 1024,      # reject huge request bodies
)
app.teardown_appcontext(db.close_db)

MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def is_valid_email(email):
    """Validate email syntax without allowing malformed consecutive/edge dots."""
    if not email or len(email) > 255 or not EMAIL_RE.match(email) or ".." in email:
        return False
    local, domain = email.split("@", 1)
    if local.startswith(".") or local.endswith("."):
        return False
    if domain.startswith("-") or domain.endswith("-"):
        return False
    return True


def validate_password(password):
    """Enforce password strength requirements; return an error string or None."""
    if len(password) < 8 or len(password) > 128:
        return "Password must be between 8 and 128 characters."
    if not re.search(r"[A-Z]", password):
        return "Password must include at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return "Password must include at least one lowercase letter."
    if not re.search(r"\d", password):
        return "Password must include at least one number."
    if not re.search(r"[^A-Za-z0-9\s]", password):
        return "Password must include at least one special character (e.g. @, #, !)."
    return None


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def parse_dt(value):
    """SQLite returns text, Azure SQL returns datetime; accept both."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


@app.template_filter("dt")
def fmt_dt(value):
    if not value:
        return ""
    try:
        return parse_dt(value).strftime("%d %b %Y, %I:%M %p")
    except (ValueError, TypeError):
        return str(value)


def escape_like(text):
    """Escape SQL LIKE wildcard characters so search matches literal user input."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def clean_text_param(value, max_len):
    """Strip null bytes and whitespace and cap parameter length safely."""
    return (value or "").replace("\x00", "").strip()[:max_len]


SORT_OPTIONS = {
    "deadline": "deadline ASC",
    "portions": "remaining_quantity DESC, deadline ASC",
    "newest": "created_at DESC, id DESC",
}


def enrich_open_donation(row, now):
    """Attach display status, availability, and deadline urgency metadata."""
    row["status"] = "available"
    total_mins = max(0, int((parse_dt(row["deadline"]) - now).total_seconds() // 60))
    hours, mins = divmod(total_mins, 60)
    days, rem_hours = divmod(hours, 24)
    if days >= 1:
        time_left = f"{days}d {rem_hours}h left"
    elif hours >= 1:
        time_left = f"{hours}h {mins}m left"
    else:
        time_left = f"{max(1, mins)}m left"

    if total_mins <= 360:
        row["urgency_level"] = "urgent"
        row["urgency_label"] = f"Urgent · {time_left}"
    elif total_mins <= 1440:
        row["urgency_level"] = "soon"
        row["urgency_label"] = f"Collect today · {time_left}"
    else:
        row["urgency_level"] = "normal"
        row["urgency_label"] = time_left

    low_stock = row["remaining_quantity"] <= 5 or row["remaining_quantity"] * 4 <= row["quantity"]
    row["availability_level"] = "low" if low_stock else "good"
    row["availability_label"] = "Low stock" if low_stock else "Available now"
    return row


def effective_donation_status(donation):
    """Compute display status, marking overdue available listings as expired."""
    if donation["status"] == "available" and parse_dt(donation["deadline"]) <= datetime.now():
        return "expired"
    return donation["status"]


def parse_donation_form(form_data, categories):
    """Validate donation form inputs shared by create and edit routes."""
    food_name = form_data.get("food_name", "").strip()
    description = form_data.get("description", "").strip()
    quantity_text = form_data.get("quantity", "").strip()
    location = form_data.get("location", "").strip()
    deadline_text = form_data.get("deadline", "").strip()
    category_text = form_data.get("category_id", "").strip()
    form = {
        "food_name": food_name,
        "description": description,
        "quantity": quantity_text,
        "location": location,
        "deadline": deadline_text,
        "category_id": category_text,
    }

    if not food_name or not location or not deadline_text:
        return None, form, "Please fill in all required fields."
    if len(food_name) > 100 or len(location) > 200:
        return None, form, "Food name or location is too long."
    if len(description) > 500:
        return None, form, "Description must be 500 characters or fewer."

    try:
        quantity = int(quantity_text)
        category_id = int(category_text)
    except ValueError:
        return None, form, "Please enter a valid quantity and choose a category."

    if not 1 <= quantity <= 100000:
        return None, form, "Quantity must be between 1 and 100000."

    try:
        deadline = datetime.fromisoformat(deadline_text)
        if deadline.tzinfo is not None:
            deadline = deadline.astimezone().replace(tzinfo=None)
    except (ValueError, TypeError):
        return None, form, "Please enter a valid collection deadline."

    if deadline <= datetime.now():
        return None, form, "The collection deadline must be in the future."
    if not any(c["id"] == category_id for c in categories):
        return None, form, "Please choose a valid category."

    return {
        "food_name": food_name,
        "description": description,
        "quantity": quantity,
        "category_id": category_id,
        "location": location,
        "deadline": deadline.isoformat(timespec="seconds"),
    }, form, None


def load_donation_or_404(donation_id):
    """Fetch a donation with category, donor name, and request count."""
    donation = db.query_one(
        "SELECT d.id, d.donor_id, d.category_id, d.food_name, d.description, "
        "d.quantity, d.remaining_quantity, d.location, d.deadline, "
        "d.status, d.created_at, c.name AS category, u.full_name AS donor_name, "
        "(SELECT COUNT(*) FROM requests r WHERE r.donation_id = d.id) AS request_count "
        "FROM donations d "
        "JOIN categories c ON c.id = d.category_id "
        "JOIN users u      ON u.id = d.donor_id "
        "WHERE d.id = ?",
        (donation_id,),
    )
    if donation is None:
        abort(404, "Donation not found.")
    donation["display_status"] = effective_donation_status(donation)
    return donation


def require_donation_owner_or_admin(donation):
    """Ensure only the owning donor or an administrator can modify a donation."""
    if g.user["role"] != "admin" and donation["donor_id"] != g.user["id"]:
        abort(403, "Only the donation owner or an administrator can manage this donation.")


# ---------------------------------------------------------------- CSRF + headers
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def claim_token():
    """Issue a single-use nonce for claim forms to prevent accidental double submissions."""
    tok = secrets.token_hex(12)
    tokens = session.get("claim_tokens", [])
    tokens.append(tok)
    session["claim_tokens"] = tokens[-10:]
    session.modified = True
    return tok


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.globals["claim_token"] = claim_token


def create_notification(user_id, event_type, message, donation_id=None, request_id=None, dedup_key=None):
    """Queue an in-app notification inside the current transaction."""
    if dedup_key is not None:
        exists = db.query_one(
            "SELECT 1 AS ok FROM notifications WHERE user_id = ? AND dedup_key = ?",
            (user_id, dedup_key),
        )
        if exists:
            return False
    db.execute(
        "INSERT INTO notifications "
        "(user_id, donation_id, request_id, event_type, message, is_read, dedup_key, created_at) "
        "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
        (user_id, donation_id, request_id, event_type[:40], message[:500], dedup_key, now_iso()),
    )
    return True


def sync_deadline_notifications_for_user(user_id):
    """Generate one-time approaching-deadline and expiry notifications for the user."""
    now = datetime.now()
    now_str = now.isoformat(timespec="seconds")
    soon_str = (now + timedelta(hours=6)).isoformat(timespec="seconds")
    inserted = False

    try:
        donor_listings = db.query(
            "SELECT id, food_name, quantity, remaining_quantity, deadline "
            "FROM donations "
            "WHERE donor_id = ? AND status = 'available' AND deadline <= ?",
            (user_id, soon_str),
        )
        for d in donor_listings:
            dl = parse_dt(d["deadline"])
            if dl <= now:
                msg = (
                    f"Your donation '{d['food_name']}' (#{d['id']}) has expired "
                    f"({d['remaining_quantity']} of {d['quantity']} portion(s) unclaimed)."
                )
                inserted = create_notification(
                    user_id,
                    "DONATION_EXPIRED",
                    msg,
                    donation_id=d["id"],
                    dedup_key=f"expired:donation:{d['id']}",
                ) or inserted
            elif d["remaining_quantity"] > 0 and d["deadline"] > now_str:
                msg = (
                    f"Your donation '{d['food_name']}' (#{d['id']}) is approaching its "
                    f"deadline ({fmt_dt(d['deadline'])})."
                )
                inserted = create_notification(
                    user_id,
                    "DONATION_EXPIRING_SOON",
                    msg,
                    donation_id=d["id"],
                    dedup_key=f"expiring_soon:donation:{d['id']}",
                ) or inserted

        rec_requests = db.query(
            "SELECT r.id, r.donation_id, d.food_name, d.deadline "
            "FROM requests r JOIN donations d ON d.id = r.donation_id "
            "WHERE r.recipient_id = ? AND r.status IN ('pending', 'accepted') "
            "AND d.status != 'cancelled' AND d.deadline <= ?",
            (user_id, soon_str),
        )
        for req in rec_requests:
            dl = parse_dt(req["deadline"])
            if dl <= now:
                msg = (
                    f"Donation '{req['food_name']}' (#{req['donation_id']}) for "
                    f"Request #{req['id']} has expired."
                )
                inserted = create_notification(
                    user_id,
                    "DONATION_EXPIRED",
                    msg,
                    donation_id=req["donation_id"],
                    request_id=req["id"],
                    dedup_key=f"expired:request:{req['id']}",
                ) or inserted
            else:
                msg = (
                    f"Reminder: Pickup deadline for '{req['food_name']}' "
                    f"(Request #{req['id']}) is approaching ({fmt_dt(req['deadline'])})."
                )
                inserted = create_notification(
                    user_id,
                    "DONATION_EXPIRING_SOON",
                    msg,
                    donation_id=req["donation_id"],
                    request_id=req["id"],
                    dedup_key=f"expiring_soon:request:{req['id']}",
                ) or inserted

        if inserted:
            db.commit()
    except Exception:
        db.rollback()


@app.before_request
def load_user_and_check_csrf():
    g.user = None
    g.unread_notifications = 0
    if "user_id" in session:
        g.user = db.query_one(
            "SELECT id, full_name, email, role, is_active FROM users WHERE id = ?",
            (session["user_id"],),
        )
        if g.user is None or not g.user["is_active"]:
            session.clear()
            g.user = None

    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Invalid or missing CSRF token.")

    if g.user is not None and request.method == "GET" and request.endpoint != "static":
        sync_deadline_notifications_for_user(g.user["id"])
        row = db.query_one(
            "SELECT COUNT(*) AS cnt FROM notifications WHERE user_id = ? AND is_read = 0",
            (g.user["id"],),
        )
        g.unread_notifications = row["cnt"] if row else 0


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
                abort(403, "You do not have permission to access this page.")
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

        pw_error = validate_password(password) if password else None
        if not name or len(name) > 100:
            error = "Please enter your full name (max 100 characters)."
        elif not is_valid_email(email):
            error = "Please enter a valid email address."
        elif not password:
            error = "Please enter a password."
        elif pw_error:
            error = pw_error
        elif role not in ("donor", "recipient"):   # 'admin' can never be self-selected
            error = "Please choose a valid role (donor or recipient)."
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
        if not email or not password:
            error = "Please enter both your email and password."
            return render_template("login.html", error=error)

        user = db.query_one("SELECT * FROM users WHERE email = ?", (email,))

        locked = user and user["locked_until"] and parse_dt(user["locked_until"]) > datetime.now()
        if locked:
            audit("LOGIN_LOCKED", f"email={email[:100]}", user_id=user["id"])
            db.commit()
            error = f"Account temporarily locked after too many failed attempts. Please try again after {LOCK_MINUTES} minutes."
        elif user and check_password_hash(user["password_hash"], password):
            if not user["is_active"]:
                audit("LOGIN_SUSPENDED", f"email={email[:100]}", user_id=user["id"])
                db.commit()
                error = "This account has been deactivated. Please contact an administrator."
                return render_template("login.html", error=error)
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
                if lock:
                    audit("ACCOUNT_LOCKED", f"email={email[:100]}", user_id=user["id"])
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
        parsed, form, error = parse_donation_form(request.form, categories)
        if not error:
            try:
                donation_id = db.insert(
                    "INSERT INTO donations (donor_id, category_id, food_name, description, "
                    "quantity, remaining_quantity, location, deadline, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'available', ?)",
                    (
                        g.user["id"],
                        parsed["category_id"],
                        parsed["food_name"],
                        parsed["description"],
                        parsed["quantity"],
                        parsed["quantity"],
                        parsed["location"],
                        parsed["deadline"],
                        now_iso(),
                    ),
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
    now = datetime.now()
    now_str = now.isoformat(timespec="seconds")

    q = clean_text_param(request.args.get("q", ""), 100)
    category = clean_text_param(request.args.get("category", ""), 60)
    location_filter = clean_text_param(request.args.get("location", ""), 100)
    min_qty_text = clean_text_param(request.args.get("min_qty", ""), 12)
    urgency = clean_text_param(request.args.get("urgency", ""), 10)
    if urgency not in ("6h", "24h"):
        urgency = ""
    sort_key = clean_text_param(request.args.get("sort", "deadline"), 20)
    if sort_key not in SORT_OPTIONS:
        sort_key = "deadline"

    filter_error = None
    min_qty = None
    if min_qty_text:
        try:
            parsed_qty = int(min_qty_text)
            if 1 <= parsed_qty <= 100000:
                min_qty = parsed_qty
            else:
                filter_error = "Minimum portions filter must be between 1 and 100000."
        except ValueError:
            filter_error = "Minimum portions filter must be a valid whole number."

    sql = "SELECT * FROM v_open_donations WHERE deadline > ?"
    params = [now_str]
    if q:
        safe_q = f"%{escape_like(q)}%"
        sql += (
            " AND (food_name LIKE ? ESCAPE '\\' OR location LIKE ? ESCAPE '\\'"
            " OR description LIKE ? ESCAPE '\\')"
        )
        params += [safe_q, safe_q, safe_q]
    if category:
        sql += " AND category = ?"
        params.append(category)
    if location_filter:
        sql += " AND location LIKE ? ESCAPE '\\'"
        params.append(f"%{escape_like(location_filter)}%")
    if min_qty is not None:
        sql += " AND remaining_quantity >= ?"
        params.append(min_qty)
    if urgency == "6h":
        sql += " AND deadline <= ?"
        params.append((now + timedelta(hours=6)).isoformat(timespec="seconds"))
    elif urgency == "24h":
        sql += " AND deadline <= ?"
        params.append((now + timedelta(hours=24)).isoformat(timespec="seconds"))

    sql += f" ORDER BY {SORT_OPTIONS[sort_key]}"
    listings = [enrich_open_donation(row, now) for row in db.query(sql, params)]
    has_active_filters = bool(
        q or category or location_filter or min_qty_text or urgency or sort_key != "deadline"
    )
    return render_template(
        "donations.html",
        donations=listings,
        categories=db.query("SELECT name FROM categories ORDER BY name"),
        q=q,
        selected=category,
        location_filter=location_filter,
        min_qty=min_qty_text,
        urgency=urgency,
        sort_key=sort_key,
        has_active_filters=has_active_filters,
        error=filter_error,
    )


@app.route("/donations/<int:donation_id>")
def donation_detail(donation_id):
    donation = load_donation_or_404(donation_id)
    can_manage = bool(
        g.user and (g.user["role"] == "admin" or donation["donor_id"] == g.user["id"])
    )
    requests_list = []
    if can_manage:
        requests_list = db.query(
            "SELECT r.id, r.quantity, r.status, r.created_at, u.full_name AS recipient_name "
            "FROM requests r JOIN users u ON u.id = r.recipient_id "
            "WHERE r.donation_id = ? ORDER BY r.id DESC",
            (donation_id,),
        )
    return render_template(
        "donation_detail.html",
        donation=donation,
        can_manage=can_manage,
        requests_list=requests_list,
    )


@app.route("/donations/<int:donation_id>/edit", methods=["GET", "POST"])
@login_required("donor", "admin")
def edit_donation(donation_id):
    donation = load_donation_or_404(donation_id)
    require_donation_owner_or_admin(donation)

    if donation["status"] == "cancelled":
        flash("Cancelled donations cannot be edited.", "error")
        return redirect(url_for("donation_detail", donation_id=donation_id))

    categories = db.query("SELECT id, name FROM categories ORDER BY name")
    error = None
    claimed_qty = donation["quantity"] - donation["remaining_quantity"]
    form = {
        "food_name": donation["food_name"],
        "description": donation["description"],
        "quantity": str(donation["quantity"]),
        "location": donation["location"],
        "deadline": parse_dt(donation["deadline"]).strftime("%Y-%m-%dT%H:%M"),
        "category_id": str(donation["category_id"]),
    }

    if request.method == "POST":
        parsed, form, error = parse_donation_form(request.form, categories)
        if not error and parsed["quantity"] < claimed_qty:
            error = f"Quantity cannot be less than the {claimed_qty} portion(s) already claimed."

        if not error:
            new_remaining = parsed["quantity"] - claimed_qty
            new_status = "claimed" if new_remaining == 0 else "available"
            try:
                changed = db.execute(
                    "UPDATE donations "
                    "SET category_id = ?, food_name = ?, description = ?, quantity = ?, "
                    "remaining_quantity = ?, location = ?, deadline = ?, status = ? "
                    "WHERE id = ? AND quantity = ? AND remaining_quantity = ? AND status != 'cancelled'",
                    (
                        parsed["category_id"],
                        parsed["food_name"],
                        parsed["description"],
                        parsed["quantity"],
                        new_remaining,
                        parsed["location"],
                        parsed["deadline"],
                        new_status,
                        donation_id,
                        donation["quantity"],
                        donation["remaining_quantity"],
                    ),
                )
                if changed != 1:
                    db.rollback()
                    error = "This donation was updated concurrently. Please reload and try again."
                else:
                    audit("DONATION_EDIT", f"donation_id={donation_id}")
                    db.commit()
                    flash(f"Donation #{donation_id} updated.", "success")
                    return redirect(url_for("donation_detail", donation_id=donation_id))
            except Exception:
                db.rollback()
                error = "Could not update the donation. Please try again."

    return render_template(
        "edit_donation.html",
        donation=donation,
        claimed_qty=claimed_qty,
        error=error,
        form=form,
        categories=categories,
    )


@app.route("/donations/<int:donation_id>/cancel", methods=["POST"])
@login_required("donor", "admin")
def cancel_donation(donation_id):
    donation = load_donation_or_404(donation_id)
    require_donation_owner_or_admin(donation)

    if donation["status"] == "cancelled":
        flash("This donation is already cancelled.", "error")
        return redirect(url_for("donation_detail", donation_id=donation_id))

    try:
        affected_requests = db.query(
            "SELECT id, recipient_id FROM requests "
            "WHERE donation_id = ? AND status IN ('pending', 'accepted')",
            (donation_id,),
        )
        changed = db.execute(
            "UPDATE donations SET status = 'cancelled' WHERE id = ? AND status != 'cancelled'",
            (donation_id,),
        )
        if changed != 1:
            db.rollback()
            flash("Could not cancel this donation.", "error")
            return redirect(url_for("donation_detail", donation_id=donation_id))

        db.execute(
            "UPDATE requests SET status = 'cancelled' "
            "WHERE donation_id = ? AND status IN ('pending', 'accepted')",
            (donation_id,),
        )
        for req in affected_requests:
            create_notification(
                req["recipient_id"],
                "DONATION_CANCELLED",
                f"Donation '{donation['food_name']}' (#{donation_id}) was cancelled; "
                f"your Request #{req['id']} has been cancelled.",
                donation_id=donation_id,
                request_id=req["id"],
            )
        if g.user["role"] == "admin" and donation["donor_id"] != g.user["id"]:
            create_notification(
                donation["donor_id"],
                "DONATION_CANCELLED",
                f"Your donation '{donation['food_name']}' (#{donation_id}) was cancelled by an administrator.",
                donation_id=donation_id,
            )
        audit("DONATION_CANCEL", f"donation_id={donation_id}")
        db.commit()
        flash(f"Donation #{donation_id} has been cancelled.", "success")
    except Exception:
        db.rollback()
        flash("Could not cancel this donation. Please try again.", "error")
    if request.form.get("next") == "admin_donations" and g.user["role"] == "admin":
        return redirect(url_for("admin_donations"))
    if request.form.get("next") == "my" and g.user["role"] != "admin":
        return redirect(url_for("my_activity"))
    return redirect(url_for("donation_detail", donation_id=donation_id))


@app.route("/donations/<int:donation_id>/delete", methods=["POST"])
@login_required("donor", "admin")
def delete_donation(donation_id):
    donation = load_donation_or_404(donation_id)
    require_donation_owner_or_admin(donation)

    if donation["request_count"] > 0:
        flash(
            "Cannot delete a donation that has claims or requests. You can cancel it instead.",
            "error",
        )
        return redirect(url_for("donation_detail", donation_id=donation_id))

    try:
        db.execute(
            "UPDATE notifications SET donation_id = NULL WHERE donation_id = ?",
            (donation_id,),
        )
        changed = db.execute(
            "DELETE FROM donations WHERE id = ? "
            "AND NOT EXISTS (SELECT 1 FROM requests WHERE donation_id = ?)",
            (donation_id, donation_id),
        )
        if changed != 1:
            db.rollback()
            flash(
                "Cannot delete this donation because it now has requests or no longer exists.",
                "error",
            )
            return redirect(url_for("donation_detail", donation_id=donation_id))

        audit("DONATION_DELETE", f"donation_id={donation_id}")
        db.commit()
        flash(f"Donation #{donation_id} deleted.", "success")
    except Exception:
        db.rollback()
        flash("Could not delete this donation. You can cancel it instead.", "error")
        return redirect(url_for("donation_detail", donation_id=donation_id))

    return redirect(url_for("my_activity" if g.user["role"] == "donor" else "donations"))


def _claim_redirect(donation_id, exists=True):
    if request.form.get("next") == "detail" and exists:
        return redirect(url_for("donation_detail", donation_id=donation_id))
    return redirect(url_for("donations"))


@app.route("/donations/<int:donation_id>/claim", methods=["POST"])
@login_required("recipient")
def claim(donation_id):
    """Reserve portions atomically using a single conditional UPDATE (ACID / no lost update)."""
    submitted_token = request.form.get("claim_token", "").strip()
    if submitted_token:
        tokens = session.get("claim_tokens", [])
        if submitted_token not in tokens:
            flash(
                "This reservation request was already submitted or expired. Please check your activity.",
                "error",
            )
            return redirect(url_for("my_activity"))

    try:
        qty = int(request.form.get("quantity", ""))
    except ValueError:
        qty = 0
    if not 1 <= qty <= 100000:
        flash("Enter a valid number of portions.", "error")
        return _claim_redirect(donation_id, exists=True)

    target = db.query_one(
        "SELECT id, donor_id, food_name, status, remaining_quantity, deadline "
        "FROM donations WHERE id = ?",
        (donation_id,),
    )
    if target is None:
        flash("That donation listing no longer exists.", "error")
        return redirect(url_for("donations"))
    if target["donor_id"] == g.user["id"]:
        flash("You cannot claim your own donation.", "error")
        return _claim_redirect(donation_id, exists=True)
    if target["status"] == "cancelled":
        flash("This donation has been cancelled and cannot be claimed.", "error")
        return _claim_redirect(donation_id, exists=True)
    if parse_dt(target["deadline"]) <= datetime.now():
        flash("This donation has expired and can no longer be claimed.", "error")
        return _claim_redirect(donation_id, exists=True)
    if target["status"] != "available" or target["remaining_quantity"] < qty:
        flash("Sorry, that many portions are no longer available.", "error")
        return _claim_redirect(donation_id, exists=True)

    try:
        changed = db.execute(
            "UPDATE donations SET remaining_quantity = remaining_quantity - ? "
            "WHERE id = ? AND donor_id != ? AND status = 'available' "
            "AND remaining_quantity >= ? AND deadline > ?",
            (qty, donation_id, g.user["id"], qty, now_iso()),
        )
        if changed != 1:
            db.rollback()
            flash("Sorry, that many portions are no longer available.", "error")
            return _claim_redirect(donation_id, exists=True)

        fully_claimed = db.execute(
            "UPDATE donations SET status = 'claimed' WHERE id = ? AND remaining_quantity = 0",
            (donation_id,),
        )
        req_id = db.insert(
            "INSERT INTO requests (donation_id, recipient_id, quantity, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (donation_id, g.user["id"], qty, now_iso()),
        )
        create_notification(
            target["donor_id"],
            "CLAIM_RECEIVED",
            f"{g.user['full_name']} reserved {qty} portion(s) of '{target['food_name']}' (Request #{req_id}).",
            donation_id=donation_id,
            request_id=req_id,
        )
        create_notification(
            g.user["id"],
            "CLAIM_CONFIRMED",
            f"You reserved {qty} portion(s) of '{target['food_name']}' (Request #{req_id}).",
            donation_id=donation_id,
            request_id=req_id,
        )
        if fully_claimed == 1:
            create_notification(
                target["donor_id"],
                "DONATION_FULLY_CLAIMED",
                f"Your donation '{target['food_name']}' (#{donation_id}) has been fully claimed.",
                donation_id=donation_id,
                request_id=req_id,
            )
        audit("CLAIM", f"donation_id={donation_id} qty={qty} request_id={req_id}")
        db.commit()                       # all statements succeed together...
        if submitted_token:
            tokens = session.get("claim_tokens", [])
            if submitted_token in tokens:
                tokens.remove(submitted_token)
                session["claim_tokens"] = tokens
                session.modified = True
        flash(f"Reserved {qty} portion(s). Request #{req_id} created.", "success")
    except Exception:
        db.rollback()                     # ...or none of them do (atomicity)
        flash("Could not complete your request. Please try again.", "error")
    return redirect(url_for("my_activity"))


def _load_request_or_404(req_id):
    req = db.query_one(
        "SELECT r.id, r.donation_id, r.recipient_id, r.quantity, r.status, r.created_at, "
        "d.donor_id, d.food_name, d.quantity AS donation_quantity, "
        "d.remaining_quantity, d.deadline, d.status AS donation_status "
        "FROM requests r JOIN donations d ON d.id = r.donation_id WHERE r.id = ?",
        (req_id,),
    )
    if req is None:
        abort(404, "Request not found.")
    return req


def _redirect_after_request_action(donation_id):
    target = request.form.get("next", "")
    if target == "detail":
        return redirect(url_for("donation_detail", donation_id=donation_id))
    if target == "admin_requests" and g.user["role"] == "admin":
        return redirect(url_for("admin_requests"))
    if g.user["role"] == "admin":
        return redirect(url_for("admin_requests"))
    return redirect(url_for("my_activity"))


def _restore_donation_stock(donation_id, qty):
    return db.execute(
        "UPDATE donations "
        "SET remaining_quantity = remaining_quantity + ?, "
        "status = CASE WHEN status = 'claimed' THEN 'available' ELSE status END "
        "WHERE id = ? AND remaining_quantity + ? <= quantity",
        (qty, donation_id, qty),
    )


@app.route("/requests/<int:req_id>/accept", methods=["POST"])
@login_required("donor", "admin")
def accept_request(req_id):
    req = _load_request_or_404(req_id)
    if g.user["role"] != "admin" and req["donor_id"] != g.user["id"]:
        abort(403, "Only the donation owner or an administrator can accept this request.")

    if req["donation_status"] == "cancelled":
        flash("Cannot accept a request for a cancelled donation.", "error")
        return _redirect_after_request_action(req["donation_id"])

    try:
        changed = db.execute(
            "UPDATE requests SET status = 'accepted' WHERE id = ? AND status = 'pending'",
            (req_id,),
        )
        if changed != 1:
            db.rollback()
            flash("Only pending requests can be accepted.", "error")
        else:
            create_notification(
                req["recipient_id"],
                "REQUEST_ACCEPTED",
                f"Your Request #{req_id} for '{req['food_name']}' ({req['quantity']} portion(s)) was accepted.",
                donation_id=req["donation_id"],
                request_id=req_id,
            )
            audit("REQUEST_ACCEPT", f"request_id={req_id} donation_id={req['donation_id']}")
            db.commit()
            flash(f"Request #{req_id} accepted.", "success")
    except Exception:
        db.rollback()
        flash("Could not accept the request. Please try again.", "error")
    return _redirect_after_request_action(req["donation_id"])


@app.route("/requests/<int:req_id>/reject", methods=["POST"])
@login_required("donor", "admin")
def reject_request(req_id):
    req = _load_request_or_404(req_id)
    if g.user["role"] != "admin" and req["donor_id"] != g.user["id"]:
        abort(403, "Only the donation owner or an administrator can reject this request.")

    try:
        changed = db.execute(
            "UPDATE requests SET status = 'rejected' "
            "WHERE id = ? AND status IN ('pending', 'accepted')",
            (req_id,),
        )
        if changed != 1:
            db.rollback()
            flash("This request can no longer be rejected.", "error")
            return _redirect_after_request_action(req["donation_id"])

        restored = _restore_donation_stock(req["donation_id"], req["quantity"])
        if restored != 1:
            db.rollback()
            flash("Could not restore portions while rejecting the request.", "error")
            return _redirect_after_request_action(req["donation_id"])

        create_notification(
            req["recipient_id"],
            "REQUEST_REJECTED",
            f"Your Request #{req_id} for '{req['food_name']}' ({req['quantity']} portion(s)) was rejected.",
            donation_id=req["donation_id"],
            request_id=req_id,
        )
        audit("REQUEST_REJECT", f"request_id={req_id} donation_id={req['donation_id']} qty={req['quantity']}")
        db.commit()
        flash(f"Request #{req_id} rejected and {req['quantity']} portion(s) restored.", "success")
    except Exception:
        db.rollback()
        flash("Could not reject the request. Please try again.", "error")
    return _redirect_after_request_action(req["donation_id"])


@app.route("/requests/<int:req_id>/complete", methods=["POST"])
@login_required("donor", "recipient", "admin")
def complete_request(req_id):
    req = _load_request_or_404(req_id)
    is_allowed = (
        g.user["role"] == "admin"
        or req["donor_id"] == g.user["id"]
        or req["recipient_id"] == g.user["id"]
    )
    if not is_allowed:
        abort(403, "You are not authorized to complete this request.")

    if req["donation_status"] == "cancelled":
        flash("Cannot complete a request for a cancelled donation.", "error")
        return _redirect_after_request_action(req["donation_id"])

    try:
        changed = db.execute(
            "UPDATE requests SET status = 'completed' "
            "WHERE id = ? AND status IN ('pending', 'accepted')",
            (req_id,),
        )
        if changed != 1:
            db.rollback()
            flash("This request cannot be marked completed.", "error")
        else:
            if g.user["id"] != req["recipient_id"]:
                create_notification(
                    req["recipient_id"],
                    "REQUEST_COMPLETED",
                    f"Request #{req_id} for '{req['food_name']}' has been marked completed.",
                    donation_id=req["donation_id"],
                    request_id=req_id,
                )
            if g.user["id"] != req["donor_id"]:
                create_notification(
                    req["donor_id"],
                    "REQUEST_COMPLETED",
                    f"Request #{req_id} for '{req['food_name']}' has been marked completed.",
                    donation_id=req["donation_id"],
                    request_id=req_id,
                )
            audit("REQUEST_COMPLETE", f"request_id={req_id} donation_id={req['donation_id']}")
            db.commit()
            flash(f"Request #{req_id} marked completed.", "success")
    except Exception:
        db.rollback()
        flash("Could not complete the request. Please try again.", "error")
    return _redirect_after_request_action(req["donation_id"])


@app.route("/requests/<int:req_id>/cancel", methods=["POST"])
@login_required("recipient", "admin")
def cancel_request(req_id):
    req = _load_request_or_404(req_id)
    if g.user["role"] != "admin" and req["recipient_id"] != g.user["id"]:
        abort(403, "You can only cancel your own reservation.")

    try:
        changed = db.execute(
            "UPDATE requests SET status = 'cancelled' "
            "WHERE id = ? AND status IN ('pending', 'accepted')",
            (req_id,),
        )
        if changed != 1:
            db.rollback()
            flash("This request can no longer be cancelled.", "error")
            return _redirect_after_request_action(req["donation_id"])

        restored = _restore_donation_stock(req["donation_id"], req["quantity"])
        if restored != 1:
            db.rollback()
            flash("Could not restore portions while cancelling the request.", "error")
            return _redirect_after_request_action(req["donation_id"])

        create_notification(
            req["donor_id"],
            "REQUEST_CANCELLED",
            f"Request #{req_id} for '{req['food_name']}' ({req['quantity']} portion(s)) was cancelled by the recipient.",
            donation_id=req["donation_id"],
            request_id=req_id,
        )
        audit("REQUEST_CANCEL", f"request_id={req_id} donation_id={req['donation_id']} qty={req['quantity']}")
        db.commit()
        flash(f"Request #{req_id} cancelled and {req['quantity']} portion(s) released.", "success")
    except Exception:
        db.rollback()
        flash("Could not cancel the request. Please try again.", "error")
    return _redirect_after_request_action(req["donation_id"])


@app.route("/my")
@login_required("donor", "recipient")
def my_activity():
    status_filter = clean_text_param(request.args.get("status", ""), 20)
    if g.user["role"] == "recipient":
        all_rows = db.query(
            "SELECT r.id, r.donation_id, r.quantity, r.status, r.created_at, "
            "d.food_name, d.location, d.deadline, d.status AS donation_status, "
            "c.name AS category, u.full_name AS donor_name "
            "FROM requests r "
            "JOIN donations d ON d.id = r.donation_id "
            "JOIN categories c ON c.id = d.category_id "
            "JOIN users u ON u.id = d.donor_id "
            "WHERE r.recipient_id = ? ORDER BY r.id DESC",
            (g.user["id"],),
        )
        summary = {
            "total_requests": len(all_rows),
            "active_claims": sum(1 for r in all_rows if r["status"] in ("pending", "accepted")),
            "completed_claims": sum(1 for r in all_rows if r["status"] == "completed"),
            "cancelled_rejected_claims": sum(
                1 for r in all_rows if r["status"] in ("cancelled", "rejected")
            ),
            "total_portions": sum(
                r["quantity"] for r in all_rows if r["status"] in ("pending", "accepted", "completed")
            ),
        }
        valid_filters = ("pending", "accepted", "completed", "cancelled", "rejected")
        if status_filter not in valid_filters:
            status_filter = ""
        rows = [r for r in all_rows if not status_filter or r["status"] == status_filter]
        return render_template(
            "my_activity.html",
            rows=rows,
            summary=summary,
            incoming_requests=[],
            status_filter=status_filter,
        )

    all_rows = db.query(
        "SELECT d.id, d.food_name, d.quantity, d.remaining_quantity, d.location, "
        "d.deadline, d.status, d.created_at, c.name AS category, COUNT(r.id) AS request_count "
        "FROM donations d "
        "JOIN categories c ON c.id = d.category_id "
        "LEFT JOIN requests r ON r.donation_id = d.id "
        "WHERE d.donor_id = ? "
        "GROUP BY d.id, d.food_name, d.quantity, d.remaining_quantity, d.location, "
        "d.deadline, d.status, d.created_at, c.name "
        "ORDER BY d.id DESC",
        (g.user["id"],),
    )
    for row in all_rows:
        row["display_status"] = effective_donation_status(row)

    incoming_requests = db.query(
        "SELECT r.id, r.donation_id, r.quantity, r.status, r.created_at, "
        "d.food_name, d.deadline, d.status AS donation_status, u.full_name AS recipient_name "
        "FROM requests r "
        "JOIN donations d ON d.id = r.donation_id "
        "JOIN users u ON u.id = r.recipient_id "
        "WHERE d.donor_id = ? "
        "ORDER BY r.id DESC",
        (g.user["id"],),
    )
    summary = {
        "total_donations": len(all_rows),
        "available_count": sum(1 for r in all_rows if r["display_status"] == "available"),
        "claimed_count": sum(1 for r in all_rows if r["display_status"] == "claimed"),
        "expired_count": sum(1 for r in all_rows if r["display_status"] == "expired"),
        "cancelled_count": sum(1 for r in all_rows if r["display_status"] == "cancelled"),
        "total_portions": sum(r["quantity"] for r in all_rows),
        "remaining_portions": sum(
            r["remaining_quantity"] for r in all_rows if r["display_status"] == "available"
        ),
        "total_requests_received": len(incoming_requests),
    }
    valid_filters = ("available", "claimed", "expired", "cancelled")
    if status_filter not in valid_filters:
        status_filter = ""
    rows = [r for r in all_rows if not status_filter or r["display_status"] == status_filter]
    return render_template(
        "my_activity.html",
        rows=rows,
        summary=summary,
        incoming_requests=incoming_requests,
        status_filter=status_filter,
    )


# ---------------------------------------------------------------- admin console
@app.route("/admin")
@login_required("admin")
def admin_dashboard():
    now_str = now_iso()
    user_stats = db.query_one(
        "SELECT COUNT(*) AS total_users, "
        "COALESCE(SUM(CASE WHEN role = 'donor' THEN 1 ELSE 0 END), 0) AS donor_count, "
        "COALESCE(SUM(CASE WHEN role = 'recipient' THEN 1 ELSE 0 END), 0) AS recipient_count, "
        "COALESCE(SUM(CASE WHEN role = 'admin' THEN 1 ELSE 0 END), 0) AS admin_count, "
        "COALESCE(SUM(CASE WHEN is_active = 0 THEN 1 ELSE 0 END), 0) AS suspended_count "
        "FROM users"
    )
    donation_stats = db.query_one(
        "SELECT COUNT(*) AS total_donations, "
        "COALESCE(SUM(CASE WHEN status = 'available' AND deadline > ? THEN 1 ELSE 0 END), 0) AS available_count, "
        "COALESCE(SUM(CASE WHEN status = 'claimed' THEN 1 ELSE 0 END), 0) AS claimed_count, "
        "COALESCE(SUM(CASE WHEN status = 'available' AND deadline <= ? THEN 1 ELSE 0 END), 0) AS expired_count, "
        "COALESCE(SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END), 0) AS cancelled_count, "
        "COALESCE(SUM(quantity), 0) AS total_portions_donated, "
        "COALESCE(SUM(quantity - remaining_quantity), 0) AS total_portions_claimed "
        "FROM donations",
        (now_str, now_str),
    )
    request_stats = db.query_one(
        "SELECT COUNT(*) AS total_requests, "
        "COALESCE(SUM(CASE WHEN r.status IN ('pending', 'accepted') THEN 1 ELSE 0 END), 0) AS active_requests, "
        "COALESCE(SUM(CASE WHEN r.status = 'completed' THEN 1 ELSE 0 END), 0) AS completed_requests, "
        "COALESCE(SUM(CASE WHEN r.status IN ('pending', 'accepted') AND (d.status = 'cancelled' OR d.deadline <= ?) "
        "    THEN 1 ELSE 0 END), 0) AS problematic_requests "
        "FROM requests r JOIN donations d ON d.id = r.donation_id",
        (now_str,),
    )
    page_sql, page_params = db.paginate_sql(10, 0)
    recent_events = db.query(
        "SELECT a.id, a.action, a.detail, a.ip_address, a.created_at, u.email "
        "FROM audit_log a LEFT JOIN users u ON u.id = a.user_id "
        f"ORDER BY a.id DESC {page_sql}",
        page_params,
    )
    return render_template(
        "admin_dashboard.html",
        user_stats=user_stats,
        donation_stats=donation_stats,
        request_stats=request_stats,
        recent_events=recent_events,
    )


@app.route("/admin/users")
@login_required("admin")
def admin_users():
    q = clean_text_param(request.args.get("q", ""), 100)
    role = clean_text_param(request.args.get("role", ""), 20)
    status = clean_text_param(request.args.get("status", ""), 20)
    if role not in ("donor", "recipient", "admin"):
        role = ""
    if status not in ("active", "suspended"):
        status = ""

    sql = (
        "SELECT u.id, u.full_name, u.email, u.role, u.is_active, u.created_at, "
        "(SELECT COUNT(*) FROM donations d WHERE d.donor_id = u.id) AS donation_count, "
        "(SELECT COUNT(*) FROM requests r WHERE r.recipient_id = u.id) AS request_count "
        "FROM users u WHERE 1 = 1"
    )
    params = []
    if q:
        safe_q = f"%{escape_like(q)}%"
        sql += " AND (u.full_name LIKE ? ESCAPE '\\' OR u.email LIKE ? ESCAPE '\\')"
        params += [safe_q, safe_q]
    if role:
        sql += " AND u.role = ?"
        params.append(role)
    if status == "active":
        sql += " AND u.is_active = 1"
    elif status == "suspended":
        sql += " AND u.is_active = 0"
    sql += " ORDER BY u.id DESC"
    return render_template(
        "admin_users.html",
        users=db.query(sql, params),
        q=q,
        role=role,
        status=status,
    )


@app.route("/admin/users/<int:user_id>/toggle-status", methods=["POST"])
@login_required("admin")
def admin_toggle_user(user_id):
    if user_id == g.user["id"]:
        flash("You cannot deactivate your own administrator account.", "error")
        return redirect(url_for("admin_users"))

    target = db.query_one("SELECT id, email, role, is_active FROM users WHERE id = ?", (user_id,))
    if target is None:
        abort(404, "User not found.")

    new_state = 0 if target["is_active"] else 1
    action = "USER_ACTIVATE" if new_state else "USER_SUSPEND"
    try:
        db.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_state, user_id))
        audit(action, f"target_user_id={user_id} email={target['email']}")
        db.commit()
        label = "activated" if new_state else "suspended"
        flash(f"User {target['email']} has been {label}.", "success")
    except Exception:
        db.rollback()
        flash("Could not update user status.", "error")
    return redirect(url_for("admin_users"))


@app.route("/admin/donations")
@login_required("admin")
def admin_donations():
    q = clean_text_param(request.args.get("q", ""), 100)
    category = clean_text_param(request.args.get("category", ""), 60)
    status_filter = clean_text_param(request.args.get("status", ""), 20)
    if status_filter not in ("available", "claimed", "expired", "cancelled"):
        status_filter = ""

    sql = (
        "SELECT d.id, d.food_name, d.quantity, d.remaining_quantity, d.location, "
        "d.deadline, d.status, d.created_at, c.name AS category, "
        "u.full_name AS donor_name, u.email AS donor_email, COUNT(r.id) AS request_count "
        "FROM donations d "
        "JOIN categories c ON c.id = d.category_id "
        "JOIN users u ON u.id = d.donor_id "
        "LEFT JOIN requests r ON r.donation_id = d.id "
        "WHERE 1 = 1"
    )
    params = []
    if q:
        safe_q = f"%{escape_like(q)}%"
        sql += (
            " AND (d.food_name LIKE ? ESCAPE '\\' OR d.location LIKE ? ESCAPE '\\'"
            " OR u.full_name LIKE ? ESCAPE '\\')"
        )
        params += [safe_q, safe_q, safe_q]
    if category:
        sql += " AND c.name = ?"
        params.append(category)
    sql += (
        " GROUP BY d.id, d.food_name, d.quantity, d.remaining_quantity, d.location, "
        "d.deadline, d.status, d.created_at, c.name, u.full_name, u.email "
        "ORDER BY d.id DESC"
    )
    rows = db.query(sql, params)
    for row in rows:
        row["display_status"] = effective_donation_status(row)
    if status_filter:
        rows = [r for r in rows if r["display_status"] == status_filter]

    return render_template(
        "admin_donations.html",
        donations=rows,
        categories=db.query("SELECT name FROM categories ORDER BY name"),
        q=q,
        selected_category=category,
        status_filter=status_filter,
    )


@app.route("/admin/requests")
@login_required("admin")
def admin_requests():
    status_filter = clean_text_param(request.args.get("status", ""), 20)
    problematic_only = request.args.get("problematic", "") == "1"
    if status_filter not in ("pending", "accepted", "rejected", "completed", "cancelled"):
        status_filter = ""

    rows = db.query(
        "SELECT r.id, r.donation_id, r.quantity, r.status, r.created_at, "
        "d.food_name, d.deadline, d.status AS donation_status, d.quantity AS donation_quantity, "
        "d.remaining_quantity, rec.full_name AS recipient_name, rec.email AS recipient_email, "
        "don.full_name AS donor_name "
        "FROM requests r "
        "JOIN donations d ON d.id = r.donation_id "
        "JOIN users rec ON rec.id = r.recipient_id "
        "JOIN users don ON don.id = d.donor_id "
        "ORDER BY r.id DESC"
    )
    now = datetime.now()
    for row in rows:
        issues = []
        if row["status"] in ("pending", "accepted"):
            if row["donation_status"] == "cancelled":
                issues.append("Parent donation cancelled")
            elif parse_dt(row["deadline"]) <= now:
                issues.append("Overdue (donation expired)")
        if row["quantity"] > row["donation_quantity"]:
            issues.append("Quantity exceeds donation")
        row["issues"] = issues

    if status_filter:
        rows = [r for r in rows if r["status"] == status_filter]
    if problematic_only:
        rows = [r for r in rows if r["issues"]]

    return render_template(
        "admin_requests.html",
        requests_list=rows,
        status_filter=status_filter,
        problematic_only=problematic_only,
    )


@app.route("/admin/audit")
@login_required("admin")
def audit_view():
    action_filter = clean_text_param(request.args.get("action", ""), 50)
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    per_page = 50
    offset = (page - 1) * per_page

    where = " WHERE a.action = ?" if action_filter else ""
    where_params = [action_filter] if action_filter else []
    total_row = db.query_one(
        f"SELECT COUNT(*) AS cnt FROM audit_log a{where}",
        where_params,
    )
    total = total_row["cnt"] if total_row else 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
        offset = (page - 1) * per_page

    page_sql, page_params = db.paginate_sql(per_page, offset)
    rows = db.query(
        "SELECT a.id, a.action, a.detail, a.ip_address, a.created_at, u.email "
        "FROM audit_log a LEFT JOIN users u ON u.id = a.user_id"
        f"{where} ORDER BY a.id DESC {page_sql}",
        list(where_params) + list(page_params),
    )
    actions = db.query("SELECT DISTINCT action FROM audit_log ORDER BY action")
    return render_template(
        "audit.html",
        rows=rows,
        page=page,
        total_pages=total_pages,
        total=total,
        action_filter=action_filter,
        actions=actions,
    )


# ---------------------------------------------------------------- notifications
@app.route("/notifications")
@login_required()
def notifications_view():
    filter_mode = request.args.get("filter", "all").strip()
    if filter_mode not in ("all", "unread"):
        filter_mode = "all"
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1

    per_page = 25
    where = "WHERE user_id = ?"
    params = [g.user["id"]]
    if filter_mode == "unread":
        where += " AND is_read = 0"

    total_row = db.query_one(f"SELECT COUNT(*) AS cnt FROM notifications {where}", params)
    total = total_row["cnt"] if total_row else 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page

    page_sql, page_params = db.paginate_sql(per_page, offset)
    rows = db.query(
        "SELECT id, donation_id, request_id, event_type, message, is_read, created_at "
        f"FROM notifications {where} ORDER BY id DESC {page_sql}",
        list(params) + list(page_params),
    )
    return render_template(
        "notifications.html",
        notifications=rows,
        filter_mode=filter_mode,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@app.route("/notifications/<int:notif_id>/read", methods=["POST"])
@login_required()
def notification_mark_read(notif_id):
    notif = db.query_one("SELECT id, user_id, is_read FROM notifications WHERE id = ?", (notif_id,))
    if notif is None:
        abort(404, "Notification not found.")
    if notif["user_id"] != g.user["id"]:
        abort(403, "You can only manage your own notifications.")

    try:
        db.execute(
            "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
            (notif_id, g.user["id"]),
        )
        db.commit()
    except Exception:
        db.rollback()
        flash("Could not update notification.", "error")
    return redirect(url_for("notifications_view", filter=request.form.get("filter", "all")))


@app.route("/notifications/read-all", methods=["POST"])
@login_required()
def notifications_mark_all_read():
    try:
        db.execute(
            "UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0",
            (g.user["id"],),
        )
        db.commit()
        flash("All notifications marked as read.", "success")
    except Exception:
        db.rollback()
        flash("Could not mark notifications as read.", "error")
    return redirect(url_for("notifications_view"))


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(413)
@app.errorhandler(500)
def http_error(err):
    if not hasattr(g, "user"):
        g.user = None
    if not hasattr(g, "unread_notifications"):
        g.unread_notifications = 0
    code = getattr(err, "code", 500)
    if code == 500:
        try:
            db.rollback()
        except Exception:
            pass
    return render_template("error.html", err=err), code


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
    app.run(debug=not is_production())
