"""Baseline stabilization test suite for SmartSurplus (Phase 1).

Runs in complete isolation using a temporary SQLite directory so the local
development/demo database in instance/smartsurplus_v2.db is never altered.

Run with:
    .venv/bin/python -m unittest discover -s tests -v
"""
import os
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

# Ensure baseline tests run against temporary SQLite, never external Azure SQL
os.environ.pop("AZURE_SQL_CONNECTION_STRING", None)

import app as app_mod
import db


class SmartSurplusBaselineTests(unittest.TestCase):
    """End-to-end baseline tests for existing SmartSurplus routes and security."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_instance = app_mod.app.instance_path
        app_mod.app.instance_path = self.tmp_dir.name
        app_mod.app.config["TESTING"] = True

        with app_mod.app.app_context():
            db.init_db()

        self.client = app_mod.app.test_client()

    def tearDown(self):
        app_mod.app.instance_path = self.orig_instance
        app_mod.app.config["TESTING"] = False
        self.tmp_dir.cleanup()

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _csrf(response):
        html = response.get_data(as_text=True)
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
        return match.group(1) if match else ""

    def _register(self, full_name, email, password, role):
        csrf = self._csrf(self.client.get("/register"))
        return self.client.post(
            "/register",
            data={
                "csrf_token": csrf,
                "full_name": full_name,
                "email": email,
                "password": password,
                "role": role,
            },
            follow_redirects=True,
        )

    def _login(self, email, password):
        csrf = self._csrf(self.client.get("/login"))
        return self.client.post(
            "/login",
            data={"csrf_token": csrf, "email": email, "password": password},
            follow_redirects=True,
        )

    def _logout(self):
        csrf = self._csrf(self.client.get("/donations"))
        return self.client.post(
            "/logout",
            data={"csrf_token": csrf},
            follow_redirects=True,
        )

    def _create_user_direct(self, full_name, email, password, role):
        with app_mod.app.app_context():
            uid = db.insert(
                "INSERT INTO users (full_name, email, password_hash, role, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (full_name, email, generate_password_hash(password), role, app_mod.now_iso()),
            )
            db.commit()
            return uid

    # -------------------------------------------------------- DB & CLI checks
    def test_database_initializes_schema_and_categories(self):
        db_path = os.path.join(self.tmp_dir.name, "smartsurplus_v2.db")
        self.assertTrue(os.path.exists(db_path))

        conn = sqlite3.connect(db_path)
        objects = {
            (row[0], row[1])
            for row in conn.execute(
                "SELECT type, name FROM sqlite_master"
            ).fetchall()
        }
        categories = [
            row[0]
            for row in conn.execute("SELECT name FROM categories ORDER BY id").fetchall()
        ]
        conn.close()

        for table in ("users", "categories", "donations", "requests", "audit_log"):
            self.assertIn(("table", table), objects)
        self.assertIn(("view", "v_open_donations"), objects)
        self.assertEqual(len(categories), 6)

    def test_cli_commands_registered_and_make_admin_works(self):
        self.assertIn("init-azure-db", app_mod.app.cli.commands)
        self.assertIn("make-admin", app_mod.app.cli.commands)

        self._create_user_direct("Promoted", "promote@test.com", "Password123", "donor")
        runner = app_mod.app.test_cli_runner()
        result = runner.invoke(args=["make-admin", "promote@test.com"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Done.", result.output)

        with app_mod.app.app_context():
            user = db.query_one("SELECT role FROM users WHERE email = ?", ("promote@test.com",))
            self.assertEqual(user["role"], "admin")

    # ---------------------------------------------------- public pages & sec
    def test_public_pages_and_security_headers(self):
        for path in ("/", "/donations", "/login", "/register"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)

        resp = self.client.get("/")
        self.assertIn("Content-Security-Policy", resp.headers)
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("Referrer-Policy"), "same-origin")

    def test_protected_routes_require_login(self):
        self.assertEqual(self.client.get("/donate").status_code, 302)
        self.assertEqual(self.client.get("/my").status_code, 302)
        self.assertEqual(self.client.get("/admin/audit").status_code, 302)

    def test_csrf_required_on_post_routes(self):
        resp = self.client.post("/login", data={"email": "a@b.com", "password": "Password@123"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"400 - Bad Request", resp.data)

    # -------------------------------------------------- auth & lockout checks
    def test_registration_validation_and_role_escalation_blocked(self):
        csrf = self._csrf(self.client.get("/register"))
        resp = self.client.post(
            "/register",
            data={
                "csrf_token": csrf,
                "full_name": "Attacker",
                "email": "attacker@test.com",
                "password": "Password@123",
                "role": "admin",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Please choose a valid role", resp.data)

        ok = self._register("Green Cafe", "cafe@test.com", "Password@123", "donor")
        self.assertIn(b"Account created. Please log in.", ok.data)

        dup = self._register("Green Cafe 2", "cafe@test.com", "Password@123", "donor")
        self.assertIn(b"An account with this email already exists.", dup.data)

    def test_login_logout_and_lockout(self):
        self._register("Bakery", "bakery@test.com", "Secret@123", "donor")

        ok = self._login("bakery@test.com", "Secret@123")
        self.assertIn(b"Bakery (donor)", ok.data)

        out = self._logout()
        self.assertIn(b"Good Food Deserves", out.data)
        self.assertEqual(self.client.get("/my").status_code, 302)

        for _ in range(5):
            bad = self._login("bakery@test.com", "WrongPassword")
            self.assertIn(b"Invalid email or password.", bad.data)

        locked = self._login("bakery@test.com", "Secret@123")
        self.assertIn(b"Account temporarily locked", locked.data)

    # ------------------------------------------- donation, search, claim, rbac
    def test_end_to_end_donor_recipient_admin_workflow(self):
        self._register("Donor Cafe", "donor1@test.com", "Password@123", "donor")
        self._register("Hope NGO", "ngo1@test.com", "Password@123", "recipient")
        self._create_user_direct("Sys Admin", "admin@test.com", "Password@123", "admin")

        # Donor creates donation
        self._login("donor1@test.com", "Password@123")
        self.assertEqual(self.client.get("/admin/audit").status_code, 403)

        future = (datetime.now() + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M")
        csrf = self._csrf(self.client.get("/donate"))
        created = self.client.post(
            "/donate",
            data={
                "csrf_token": csrf,
                "food_name": "Veg Meals",
                "category_id": "1",
                "quantity": "15",
                "location": "Main Street",
                "deadline": future,
            },
            follow_redirects=True,
        )
        self.assertIn(b"Donation #1 saved successfully!", created.data)
        self.assertIn(b"Veg Meals", created.data)

        # Past deadline rejected
        past = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        csrf = self._csrf(self.client.get("/donate"))
        bad_dt = self.client.post(
            "/donate",
            data={
                "csrf_token": csrf,
                "food_name": "Expired Meals",
                "category_id": "1",
                "quantity": "5",
                "location": "Main Street",
                "deadline": past,
            },
        )
        self.assertIn(b"collection deadline must be in the future", bad_dt.data)

        # Donor sees listing on /my
        my_donor = self.client.get("/my")
        self.assertEqual(my_donor.status_code, 200)
        self.assertIn(b"My listings", my_donor.data)
        self.assertIn(b"Veg Meals", my_donor.data)
        self._logout()

        # Recipient browses, searches, and claims
        self._login("ngo1@test.com", "Password@123")
        self.assertEqual(self.client.get("/donate").status_code, 403)

        search_hit = self.client.get("/donations?q=Veg&category=Cooked+meals")
        self.assertIn(b"Veg Meals", search_hit.data)

        # Over-claim blocked atomically
        csrf = self._csrf(self.client.get("/donations"))
        over = self.client.post(
            "/donations/1/claim",
            data={"csrf_token": csrf, "quantity": "20"},
            follow_redirects=True,
        )
        self.assertIn(b"no longer available", over.data)

        # Valid partial claim
        partial = self.client.post(
            "/donations/1/claim",
            data={"csrf_token": csrf, "quantity": "5"},
            follow_redirects=True,
        )
        self.assertIn(b"Reserved 5 portion(s).", partial.data)
        self.assertIn(b"My reservations", partial.data)

        # Claim remaining 10 -> listing disappears from open donations
        csrf = self._csrf(self.client.get("/donations"))
        final_claim = self.client.post(
            "/donations/1/claim",
            data={"csrf_token": csrf, "quantity": "10"},
            follow_redirects=True,
        )
        self.assertIn(b"Reserved 10 portion(s).", final_claim.data)
        self.assertNotIn(b"Veg Meals", self.client.get("/donations").data)
        self._logout()

        # Admin views audit log and cannot access /my
        self._login("admin@test.com", "Password@123")
        self.assertEqual(self.client.get("/my").status_code, 403)
        audit = self.client.get("/admin/audit")
        self.assertEqual(audit.status_code, 200)
        self.assertIn(b"DONATION_CREATE", audit.data)
        self.assertIn(b"CLAIM", audit.data)

    # ---------------------------------------------------- Phase 1 bug fixes
    def test_fix1_donate_accepts_timezone_aware_iso_deadline(self):
        self._create_user_direct("Donor TZ", "tz@test.com", "Password@123", "donor")
        self._login("tz@test.com", "Password@123")
        csrf = self._csrf(self.client.get("/donate"))
        resp = self.client.post(
            "/donate",
            data={
                "csrf_token": csrf,
                "food_name": "UTC Bread",
                "category_id": "2",
                "quantity": "10",
                "location": "North End",
                "deadline": "2099-01-01T12:00:00Z",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"UTC Bread", resp.data)
        self.assertEqual(app_mod.fmt_dt("invalid-date"), "invalid-date")

    def test_fix2_claim_validates_upper_quantity_bound(self):
        self._create_user_direct("Donor", "d2@test.com", "Password@123", "donor")
        self._create_user_direct("NGO", "r2@test.com", "Password@123", "recipient")
        self._login("d2@test.com", "Password@123")
        csrf = self._csrf(self.client.get("/donate"))
        future = (datetime.now() + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M")
        self.client.post(
            "/donate",
            data={
                "csrf_token": csrf,
                "food_name": "Soup",
                "category_id": "1",
                "quantity": "10",
                "location": "Center",
                "deadline": future,
            },
        )
        self._logout()

        self._login("r2@test.com", "Password@123")
        csrf = self._csrf(self.client.get("/donations"))
        resp = self.client.post(
            "/donations/1/claim",
            data={"csrf_token": csrf, "quantity": "999999999999999999999"},
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Enter a valid number of portions.", resp.data)

    def test_fix3_search_escapes_like_wildcards(self):
        self._create_user_direct("Donor", "d3@test.com", "Password@123", "donor")
        self._login("d3@test.com", "Password@123")
        csrf = self._csrf(self.client.get("/donate"))
        future = (datetime.now() + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M")
        self.client.post(
            "/donate",
            data={
                "csrf_token": csrf,
                "food_name": "Fresh Apples",
                "category_id": "3",
                "quantity": "12",
                "location": "Market Road",
                "deadline": future,
            },
        )
        # '%' should NOT act as a wildcard matching "Fresh Apples"
        resp = self.client.get("/donations?q=%25")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"Fresh Apples", resp.data)
        self.assertIn(b"No donations found", resp.data)

    def test_fix4_error_handlers_404_413_500(self):
        from werkzeug.exceptions import InternalServerError

        r404 = self.client.get("/nonexistent-page")
        self.assertEqual(r404.status_code, 404)
        self.assertIn(b"404 - Not Found", r404.data)

        # Oversized body (>64KB) renders custom 413 error page
        csrf = self._csrf(self.client.get("/register"))
        r413 = self.client.post(
            "/register",
            data={
                "csrf_token": csrf,
                "full_name": "A" * (70 * 1024),
                "email": "big@test.com",
                "password": "Password@123",
                "role": "donor",
            },
        )
        self.assertEqual(r413.status_code, 413)
        self.assertIn(b"413 - Request Entity Too Large", r413.data)

        # 500 error handler renders error.html cleanly and rolls back safely
        with app_mod.app.test_request_context("/"):
            html, status = app_mod.http_error(InternalServerError())
            self.assertEqual(status, 500)
            self.assertIn("500 - Internal Server Error", html)


if __name__ == "__main__":
    unittest.main()
