"""Phase 7 Administrator Dashboard & Management tests for SmartSurplus.

Covers:
- Access control across admin, donor, recipient, and logged-out users
- Platform summary accuracy (users, donations, portions, requests, problematic count)
- User listing, search/filter, password hashes never exposed, suspend/activate + self-lockout guard
- Suspended user cannot log in and existing session is invalidated
- Admin donation listing, filtering, and cancelling listings
- Admin request monitoring and problematic record detection
- Audit log pagination and action filtering

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


class SmartSurplusAdminTests(unittest.TestCase):
    """Comprehensive tests for the Administrator Dashboard and RBAC protections."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_instance = app_mod.app.instance_path
        app_mod.app.instance_path = self.tmp_dir.name
        app_mod.app.config["TESTING"] = True

        with app_mod.app.app_context():
            db.init_db()
            self._seed_admin_test_data()

        self.client = app_mod.app.test_client()

    def tearDown(self):
        app_mod.app.instance_path = self.orig_instance
        app_mod.app.config["TESTING"] = False
        self.tmp_dir.cleanup()

    @staticmethod
    def _csrf(resp):
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.get_data(as_text=True))
        return match.group(1) if match else ""

    def _login(self, client, email, password="Strong@123"):
        csrf = self._csrf(client.get("/login"))
        return client.post(
            "/login",
            data={"csrf_token": csrf, "email": email, "password": password},
            follow_redirects=True,
        )

    def _seed_admin_test_data(self):
        now = datetime.now()
        iso = lambda hrs: (now + timedelta(hours=hrs)).isoformat(timespec="seconds")
        pw = generate_password_hash("Strong@123")

        self.donor_id = db.insert(
            "INSERT INTO users (full_name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
            ("Green Cafe", "donor@example.com", pw, "donor", iso(-5)),
        )
        self.rec_id = db.insert(
            "INSERT INTO users (full_name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
            ("Hope NGO", "ngo@example.com", pw, "recipient", iso(-5)),
        )
        self.admin_id = db.insert(
            "INSERT INTO users (full_name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
            ("Site Admin", "admin@example.com", pw, "admin", iso(-5)),
        )

        # Open donation, expired donation with overdue pending request, and cancelled donation
        self.don_open = db.insert(
            "INSERT INTO donations (donor_id, category_id, food_name, description, quantity, remaining_quantity, location, deadline, status, created_at) "
            "VALUES (?, 1, 'Fresh Meals', '', 20, 15, 'Downtown', ?, 'available', ?)",
            (self.donor_id, iso(8), iso(-2)),
        )
        self.don_exp = db.insert(
            "INSERT INTO donations (donor_id, category_id, food_name, description, quantity, remaining_quantity, location, deadline, status, created_at) "
            "VALUES (?, 2, 'Overdue Bread', '', 10, 5, 'North', ?, 'available', ?)",
            (self.donor_id, iso(-2), iso(-6)),
        )
        self.don_canc = db.insert(
            "INSERT INTO donations (donor_id, category_id, food_name, description, quantity, remaining_quantity, location, deadline, status, created_at) "
            "VALUES (?, 3, 'Cancelled Salad', '', 10, 10, 'South', ?, 'cancelled', ?)",
            (self.donor_id, iso(8), iso(-2)),
        )

        # Normal pending request + problematic overdue request + problematic cancelled-parent request
        db.insert(
            "INSERT INTO requests (donation_id, recipient_id, quantity, status, created_at) VALUES (?,?,5,'pending',?)",
            (self.don_open, self.rec_id, iso(-1)),
        )
        db.insert(
            "INSERT INTO requests (donation_id, recipient_id, quantity, status, created_at) VALUES (?,?,5,'pending',?)",
            (self.don_exp, self.rec_id, iso(-4)),
        )
        db.insert(
            "INSERT INTO requests (donation_id, recipient_id, quantity, status, created_at) VALUES (?,?,3,'accepted',?)",
            (self.don_canc, self.rec_id, iso(-1)),
        )

        # 60 audit log rows to test SQL pagination (> 50 per page)
        for i in range(60):
            db.execute(
                "INSERT INTO audit_log (user_id, action, detail, ip_address, created_at) VALUES (?,?,?,?,?)",
                (self.admin_id, "TEST_EVENT" if i % 2 == 0 else "OTHER_EVENT", f"evt={i}", "127.0.0.1", iso(-1)),
            )
        db.commit()

    def test_admin_routes_access_control_for_all_roles(self):
        admin_get_routes = ("/admin", "/admin/users", "/admin/donations", "/admin/requests", "/admin/audit")

        # 1. Logged-out -> 302 redirect to /login
        for path in admin_get_routes:
            resp = self.client.get(path, follow_redirects=False)
            self.assertEqual(resp.status_code, 302, path)
            self.assertIn("/login", resp.headers["Location"])
        csrf_anon = self._csrf(self.client.get("/login"))
        self.assertEqual(
            self.client.post(f"/admin/users/{self.donor_id}/toggle-status", data={"csrf_token": csrf_anon}).status_code,
            302,
        )

        # 2. Donor -> 403
        self._login(self.client, "donor@example.com")
        for path in admin_get_routes:
            self.assertEqual(self.client.get(path).status_code, 403, path)
        csrf_d = self._csrf(self.client.get("/donations"))
        self.assertEqual(
            self.client.post(f"/admin/users/{self.rec_id}/toggle-status", data={"csrf_token": csrf_d}).status_code,
            403,
        )
        self.client.post("/logout", data={"csrf_token": csrf_d})

        # 3. Recipient -> 403
        self._login(self.client, "ngo@example.com")
        for path in admin_get_routes:
            self.assertEqual(self.client.get(path).status_code, 403, path)
        csrf_r = self._csrf(self.client.get("/donations"))
        self.assertEqual(
            self.client.post(f"/admin/users/{self.donor_id}/toggle-status", data={"csrf_token": csrf_r}).status_code,
            403,
        )
        self.client.post("/logout", data={"csrf_token": csrf_r})

        # 4. Admin -> 200
        self._login(self.client, "admin@example.com")
        for path in admin_get_routes:
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_admin_summary_and_user_management_with_suspension(self):
        self._login(self.client, "admin@example.com")

        # Overview metrics
        overview = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("Administrator Dashboard", overview)
        self.assertIn('<span class="stat-value">3</span><span class="stat-label">Total users</span>', overview)
        self.assertIn('<span class="stat-value">40</span><span class="stat-label">Portions donated</span>', overview)
        self.assertIn('<span class="stat-value">10</span><span class="stat-label">Portions claimed</span>', overview)
        self.assertIn('<span class="stat-value">2</span><span class="stat-label">Problematic records</span>', overview)

        # Users list search & no password hashes exposed
        users_resp = self.client.get("/admin/users?q=Hope&role=recipient")
        users_html = users_resp.get_data(as_text=True)
        self.assertIn("Hope NGO", users_html)
        self.assertNotIn("Green Cafe", users_html)
        self.assertNotIn("scrypt:", users_html)
        self.assertNotIn("pbkdf2:", users_html)

        # Self-suspension blocked
        csrf = self._csrf(users_resp)
        self_toggle = self.client.post(
            f"/admin/users/{self.admin_id}/toggle-status",
            data={"csrf_token": csrf},
            follow_redirects=True,
        )
        self.assertIn(b"cannot deactivate your own administrator account", self_toggle.data)

        # Log recipient in another client, then suspend them as admin
        rec_client = app_mod.app.test_client()
        self._login(rec_client, "ngo@example.com")
        self.assertEqual(rec_client.get("/my").status_code, 200)

        suspend = self.client.post(
            f"/admin/users/{self.rec_id}/toggle-status",
            data={"csrf_token": csrf},
            follow_redirects=True,
        )
        self.assertIn(b"ngo@example.com has been suspended.", suspend.data)

        # Existing recipient session is invalidated & re-login fails cleanly
        self.assertEqual(rec_client.get("/my").status_code, 302)
        relogin = self._login(rec_client, "ngo@example.com")
        self.assertIn(b"account has been deactivated", relogin.data)

        # Reactivate recipient -> login works again
        csrf = self._csrf(self.client.get("/admin/users"))
        activate = self.client.post(
            f"/admin/users/{self.rec_id}/toggle-status",
            data={"csrf_token": csrf},
            follow_redirects=True,
        )
        self.assertIn(b"ngo@example.com has been activated.", activate.data)
        self.assertIn(b"Hope NGO (recipient)", self._login(rec_client, "ngo@example.com").data)

    def test_admin_donations_requests_monitoring_and_audit_pagination(self):
        self._login(self.client, "admin@example.com")

        # Filter admin donations & cancel inappropriate listing
        don_resp = self.client.get("/admin/donations?status=available")
        self.assertIn(b"Fresh Meals", don_resp.data)
        self.assertNotIn(b"Overdue Bread", don_resp.data)

        csrf = self._csrf(don_resp)
        cancelled = self.client.post(
            f"/donations/{self.don_open}/cancel",
            data={"csrf_token": csrf, "next": "admin_donations"},
            follow_redirects=True,
        )
        self.assertIn(b"Donation #1 has been cancelled.", cancelled.data)

        # Problematic request monitoring flags overdue & cancelled-parent requests
        prob_resp = self.client.get("/admin/requests?problematic=1")
        prob_html = prob_resp.get_data(as_text=True)
        self.assertIn("Overdue (donation expired)", prob_html)
        self.assertIn("Parent donation cancelled", prob_html)

        # Audit pagination (page 1 has 50 items, page 2 has remaining items) + action filter
        p1 = self.client.get("/admin/audit").get_data(as_text=True)
        self.assertIn("Page 1 of 2", p1)
        p2 = self.client.get("/admin/audit?page=2").get_data(as_text=True)
        self.assertIn("Page 2 of 2", p2)

        filtered = self.client.get("/admin/audit?action=TEST_EVENT").get_data(as_text=True)
        self.assertIn("30 security event(s) recorded", filtered)
        self.assertIn("<td>TEST_EVENT</td>", filtered)
        self.assertNotIn("<td>OTHER_EVENT</td>", filtered)


if __name__ == "__main__":
    unittest.main()
