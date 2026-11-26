"""Phase 2 Authentication & Authorization tests for SmartSurplus.

Covers every requirement in Phase 2:
- Valid and invalid registration (name, email, password strength, role)
- Duplicate email prevention & password hashing (never plain text)
- Valid and invalid login (generic errors, blank checks, session fixation prevention)
- Account lockout after 5 failures and unlock after lock window expires
- Logout (POST only, requires authentication, clears session)
- Role-based access control (anonymous, donor, recipient, admin)
- CSRF failure across state-changing routes
- Secure session cookie configuration

Run with:
    .venv/bin/python -m unittest discover -s tests -v
"""
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

os.environ.pop("AZURE_SQL_CONNECTION_STRING", None)

import app as app_mod
import db


class SmartSurplusAuthTests(unittest.TestCase):
    """Comprehensive authentication, authorization, and security test suite."""

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

    @staticmethod
    def _csrf(resp):
        html = resp.get_data(as_text=True)
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
        return match.group(1) if match else ""

    def _register_post(self, full_name, email, password, role, follow=True):
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
            follow_redirects=follow,
        )

    def _login_post(self, email, password, follow=True):
        csrf = self._csrf(self.client.get("/login"))
        return self.client.post(
            "/login",
            data={"csrf_token": csrf, "email": email, "password": password},
            follow_redirects=follow,
        )

    def _create_user(self, full_name, email, password, role):
        with app_mod.app.app_context():
            uid = db.insert(
                "INSERT INTO users (full_name, email, password_hash, role, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (full_name, email, generate_password_hash(password), role, app_mod.now_iso()),
            )
            db.commit()
            return uid

    # ----------------------------------------------------------- Registration
    def test_valid_registration_hashes_password_and_normalizes_email(self):
        resp_donor = self._register_post("Leaf Kitchen", "  Chef@Example.com ", "Strong@123", "donor")
        self.assertIn(b"Account created. Please log in.", resp_donor.data)

        resp_rec = self._register_post("City Shelter", "shelter@example.com", "Strong@123", "recipient")
        self.assertIn(b"Account created. Please log in.", resp_rec.data)

        with app_mod.app.app_context():
            donor = db.query_one("SELECT * FROM users WHERE email = ?", ("chef@example.com",))
            rec = db.query_one("SELECT * FROM users WHERE email = ?", ("shelter@example.com",))
            self.assertIsNotNone(donor)
            self.assertIsNotNone(rec)
            self.assertEqual(donor["role"], "donor")
            self.assertEqual(rec["role"], "recipient")
            self.assertNotEqual(donor["password_hash"], "Strong@123")
            self.assertTrue(
                donor["password_hash"].startswith("scrypt:")
                or donor["password_hash"].startswith("pbkdf2:")
            )

    def test_invalid_registration_fields_and_password_strength(self):
        bad_cases = [
            # (full_name, email, password, role, expected_snippet)
            ("", "u@example.com", "Strong@123", "donor", b"full name"),
            ("A" * 101, "u@example.com", "Strong@123", "donor", b"full name"),
            ("User", "not-an-email", "Strong@123", "donor", b"valid email"),
            ("User", "a..b@example.com", "Strong@123", "donor", b"valid email"),
            ("User", "u@example.com", "", "donor", b"enter a password"),
            ("User", "u@example.com", "Ab@1", "donor", b"between 8 and 128"),
            ("User", "u@example.com", "alllower@123", "donor", b"uppercase"),
            ("User", "u@example.com", "ALLUPPER@123", "donor", b"lowercase"),
            ("User", "u@example.com", "NoNumber@abc", "donor", b"number"),
            ("User", "u@example.com", "NoSymbol123", "donor", b"special character"),
            ("User", "u@example.com", "Strong@123", "", b"valid role"),
            ("User", "u@example.com", "Strong@123", "admin", b"valid role"),
            ("User", "u@example.com", "Strong@123", "superuser", b"valid role"),
        ]
        for name, email, pw, role, snippet in bad_cases:
            with self.subTest(name=name, email=email, pw=pw, role=role):
                resp = self._register_post(name, email, pw, role, follow=False)
                self.assertEqual(resp.status_code, 200)
                self.assertIn(snippet, resp.data)

        with app_mod.app.app_context():
            count = db.query_one("SELECT COUNT(*) AS c FROM users")["c"]
            self.assertEqual(count, 0)

    def test_duplicate_email_registration_blocked_case_insensitive(self):
        self._register_post("First User", "dup@example.com", "Strong@123", "donor")
        resp = self._register_post("Second User", "DUP@Example.COM", "Strong@123", "recipient", follow=False)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"An account with this email already exists.", resp.data)

    # -------------------------------------------------------- Login & Lockout
    def test_valid_and_invalid_login_with_generic_messages(self):
        self._register_post("Donor One", "donor@example.com", "Strong@123", "donor")

        # Blank fields
        blank = self._login_post("", "", follow=False)
        self.assertIn(b"Please enter both your email and password.", blank.data)

        # Wrong password and unknown email produce identical generic error
        wrong_pw = self._login_post("donor@example.com", "Wrong@123", follow=False)
        unknown_email = self._login_post("nobody@example.com", "Wrong@123", follow=False)
        self.assertIn(b"Invalid email or password.", wrong_pw.data)
        self.assertIn(b"Invalid email or password.", unknown_email.data)

        # Correct login rotates session & resets failed_attempts
        with self.client.session_transaction() as sess:
            sess["pre_login_marker"] = "stale"
        ok = self._login_post("DONOR@example.com", "Strong@123", follow=True)
        self.assertEqual(ok.status_code, 200)
        self.assertIn(b"Donor One (donor)", ok.data)

        with self.client.session_transaction() as sess:
            self.assertNotIn("pre_login_marker", sess)
            self.assertIn("user_id", sess)

        with app_mod.app.app_context():
            u = db.query_one("SELECT failed_attempts, locked_until FROM users WHERE email = ?", ("donor@example.com",))
            self.assertEqual(u["failed_attempts"], 0)
            self.assertIsNone(u["locked_until"])

    def test_account_lockout_blocks_and_recovers_after_expiry(self):
        self._register_post("Lock Test", "lock@example.com", "Strong@123", "recipient")

        for _ in range(5):
            bad = self._login_post("lock@example.com", "Wrong@999", follow=False)
            self.assertIn(b"Invalid email or password.", bad.data)

        # 6th attempt even with correct password is blocked
        blocked = self._login_post("lock@example.com", "Strong@123", follow=False)
        self.assertIn(b"Account temporarily locked", blocked.data)

        with app_mod.app.app_context():
            events = [
                r["action"]
                for r in db.query("SELECT action FROM audit_log ORDER BY id")
            ]
            self.assertIn("ACCOUNT_LOCKED", events)
            self.assertIn("LOGIN_LOCKED", events)

            # Simulate lock expiration in DB and verify login succeeds again
            expired = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
            db.execute("UPDATE users SET locked_until = ? WHERE email = ?", (expired, "lock@example.com"))
            db.commit()

        unlocked = self._login_post("lock@example.com", "Strong@123", follow=True)
        self.assertIn(b"Lock Test (recipient)", unlocked.data)

    # ---------------------------------------------------------------- Logout
    def test_logout_requires_auth_post_and_clears_session(self):
        # GET /logout is not allowed
        get_out = self.client.get("/logout")
        self.assertEqual(get_out.status_code, 405)

        # Unauthenticated POST /logout with valid CSRF redirects to login
        csrf = self._csrf(self.client.get("/login"))
        unauth_out = self.client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
        self.assertEqual(unauth_out.status_code, 302)
        self.assertIn("/login", unauth_out.headers["Location"])

        # Authenticated POST /logout clears session
        self._register_post("User Out", "out@example.com", "Strong@123", "donor")
        self._login_post("out@example.com", "Strong@123")
        csrf = self._csrf(self.client.get("/donations"))
        resp = self.client.post("/logout", data={"csrf_token": csrf}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Log in", resp.data)
        with self.client.session_transaction() as sess:
            self.assertEqual(dict(sess), {})
        self.assertEqual(self.client.get("/my").status_code, 302)

    # ------------------------------------------------------------------ RBAC
    def test_role_based_access_control_matrix(self):
        self._create_user("Donor", "d@example.com", "Strong@123", "donor")
        self._create_user("Recipient", "r@example.com", "Strong@123", "recipient")
        self._create_user("Admin", "a@example.com", "Strong@123", "admin")

        # Anonymous
        for protected in ("/donate", "/my", "/admin/audit"):
            resp = self.client.get(protected, follow_redirects=False)
            self.assertEqual(resp.status_code, 302, protected)
            self.assertIn("/login", resp.headers["Location"])

        csrf = self._csrf(self.client.get("/login"))
        anon_claim = self.client.post("/donations/1/claim", data={"csrf_token": csrf, "quantity": "1"})
        self.assertEqual(anon_claim.status_code, 302)

        # Donor
        self._login_post("d@example.com", "Strong@123")
        self.assertEqual(self.client.get("/donate").status_code, 200)
        self.assertEqual(self.client.get("/my").status_code, 200)
        self.assertEqual(self.client.get("/admin/audit").status_code, 403)
        csrf = self._csrf(self.client.get("/donations"))
        self.assertEqual(
            self.client.post("/donations/1/claim", data={"csrf_token": csrf, "quantity": "1"}).status_code,
            403,
        )
        self.client.post("/logout", data={"csrf_token": csrf})

        # Recipient
        self._login_post("r@example.com", "Strong@123")
        self.assertEqual(self.client.get("/donations").status_code, 200)
        self.assertEqual(self.client.get("/my").status_code, 200)
        self.assertEqual(self.client.get("/donate").status_code, 403)
        self.assertEqual(self.client.get("/admin/audit").status_code, 403)
        csrf = self._csrf(self.client.get("/donations"))
        self.client.post("/logout", data={"csrf_token": csrf})

        # Admin
        self._login_post("a@example.com", "Strong@123")
        self.assertEqual(self.client.get("/admin/audit").status_code, 200)
        self.assertEqual(self.client.get("/donate").status_code, 200)
        self.assertEqual(self.client.get("/my").status_code, 403)
        csrf = self._csrf(self.client.get("/donations"))
        self.assertEqual(
            self.client.post("/donations/1/claim", data={"csrf_token": csrf, "quantity": "1"}).status_code,
            403,
        )

    # ------------------------------------------------------- CSRF & Cookies
    def test_csrf_failure_on_all_state_changing_endpoints(self):
        self._create_user("Donor", "d@example.com", "Strong@123", "donor")
        self._login_post("d@example.com", "Strong@123")

        endpoints = [
            ("/register", {"full_name": "X", "email": "x@example.com", "password": "Strong@123", "role": "donor"}),
            ("/login", {"email": "d@example.com", "password": "Strong@123"}),
            ("/logout", {}),
            ("/donate", {"food_name": "Bread", "category_id": "1", "quantity": "5", "location": "A", "deadline": "2099-01-01T10:00"}),
            ("/donations/1/claim", {"quantity": "1"}),
        ]
        for path, payload in endpoints:
            with self.subTest(path=path, case="missing"):
                r = self.client.post(path, data=payload)
                self.assertEqual(r.status_code, 400)
                self.assertIn(b"Invalid or missing CSRF token.", r.data)
            with self.subTest(path=path, case="tampered"):
                bad_payload = dict(payload, csrf_token="tampered-token")
                r = self.client.post(path, data=bad_payload)
                self.assertEqual(r.status_code, 400)
                self.assertIn(b"Invalid or missing CSRF token.", r.data)

    def test_session_cookie_security_flags(self):
        self.assertTrue(app_mod.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(app_mod.app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self._create_user("Cookie User", "cookie@example.com", "Strong@123", "donor")
        csrf = self._csrf(self.client.get("/login"))
        resp = self.client.post(
            "/login",
            data={"csrf_token": csrf, "email": "cookie@example.com", "password": "Strong@123"},
            follow_redirects=False,
        )
        cookies = resp.headers.getlist("Set-Cookie")
        self.assertTrue(any("HttpOnly" in c and "SameSite=Lax" in c for c in cookies))


if __name__ == "__main__":
    unittest.main()
