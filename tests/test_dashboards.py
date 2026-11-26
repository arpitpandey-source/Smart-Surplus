"""Phase 6 User Dashboards & Activity Management tests for SmartSurplus.

Covers:
- Donor dashboard metrics, remaining quantities, incoming requests, and actions
- Receiver dashboard metrics, claim details, completed and cancelled/rejected claims
- Multi-user isolation across two donors and two recipients
- Empty states and status filters

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


class SmartSurplusDashboardTests(unittest.TestCase):
    """Test donor and receiver dashboards, metric accuracy, and privacy isolation."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_instance = app_mod.app.instance_path
        app_mod.app.instance_path = self.tmp_dir.name
        app_mod.app.config["TESTING"] = True

        with app_mod.app.app_context():
            db.init_db()
            self._seed_multi_user_data()

        self.client = app_mod.app.test_client()

    def tearDown(self):
        app_mod.app.instance_path = self.orig_instance
        app_mod.app.config["TESTING"] = False
        self.tmp_dir.cleanup()

    @staticmethod
    def _csrf(resp):
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.get_data(as_text=True))
        return match.group(1) if match else ""

    def _login(self, email, password="Strong@123"):
        csrf = self._csrf(self.client.get("/login"))
        return self.client.post(
            "/login",
            data={"csrf_token": csrf, "email": email, "password": password},
            follow_redirects=True,
        )

    def _logout(self):
        csrf = self._csrf(self.client.get("/donations"))
        self.client.post("/logout", data={"csrf_token": csrf})

    def _seed_multi_user_data(self):
        now = datetime.now()
        iso = lambda hrs: (now + timedelta(hours=hrs)).isoformat(timespec="seconds")
        pw = generate_password_hash("Strong@123")

        d1 = db.insert(
            "INSERT INTO users (full_name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
            ("Donor One Cafe", "d1@example.com", pw, "donor", iso(0)),
        )
        d2 = db.insert(
            "INSERT INTO users (full_name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
            ("Donor Two Bakery", "d2@example.com", pw, "donor", iso(0)),
        )
        r1 = db.insert(
            "INSERT INTO users (full_name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
            ("Recipient One NGO", "r1@example.com", pw, "recipient", iso(0)),
        )
        r2 = db.insert(
            "INSERT INTO users (full_name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
            ("Recipient Two Shelter", "r2@example.com", pw, "recipient", iso(0)),
        )
        # Empty new users for empty-state verification
        db.insert(
            "INSERT INTO users (full_name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
            ("Empty Donor", "ed@example.com", pw, "donor", iso(0)),
        )
        db.insert(
            "INSERT INTO users (full_name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
            ("Empty Receiver", "er@example.com", pw, "recipient", iso(0)),
        )

        # Donor 1: 4 donations (available, claimed, expired, cancelled) -> total portions = 40+20+10+15 = 85
        d1_avail = db.insert(
            "INSERT INTO donations (donor_id, category_id, food_name, description, quantity, remaining_quantity, location, deadline, status, created_at) "
            "VALUES (?, 1, 'D1 Rice', '', 40, 25, 'Loc 1', ?, 'available', ?)",
            (d1, iso(8), iso(-1)),
        )
        d1_claimed = db.insert(
            "INSERT INTO donations (donor_id, category_id, food_name, description, quantity, remaining_quantity, location, deadline, status, created_at) "
            "VALUES (?, 2, 'D1 Rolls', '', 20, 0, 'Loc 1', ?, 'claimed', ?)",
            (d1, iso(8), iso(-1)),
        )
        db.insert(
            "INSERT INTO donations (donor_id, category_id, food_name, description, quantity, remaining_quantity, location, deadline, status, created_at) "
            "VALUES (?, 3, 'D1 Expired Fruit', '', 10, 10, 'Loc 1', ?, 'available', ?)",
            (d1, iso(-3), iso(-5)),
        )
        db.insert(
            "INSERT INTO donations (donor_id, category_id, food_name, description, quantity, remaining_quantity, location, deadline, status, created_at) "
            "VALUES (?, 4, 'D1 Cancelled Snacks', '', 15, 15, 'Loc 1', ?, 'cancelled', ?)",
            (d1, iso(8), iso(-1)),
        )

        # Donor 2: 1 private donation
        d2_avail = db.insert(
            "INSERT INTO donations (donor_id, category_id, food_name, description, quantity, remaining_quantity, location, deadline, status, created_at) "
            "VALUES (?, 5, 'D2 Secret Milk', '', 50, 40, 'Loc 2', ?, 'available', ?)",
            (d2, iso(8), iso(-1)),
        )

        # Claims:
        # r1 has 3 claims: pending(15 on D1 Rice), completed(20 on D1 Rolls), rejected(5 on D1 Rice)
        db.insert(
            "INSERT INTO requests (donation_id, recipient_id, quantity, status, created_at) VALUES (?,?,15,'pending',?)",
            (d1_avail, r1, iso(-1)),
        )
        db.insert(
            "INSERT INTO requests (donation_id, recipient_id, quantity, status, created_at) VALUES (?,?,20,'completed',?)",
            (d1_claimed, r1, iso(-1)),
        )
        db.insert(
            "INSERT INTO requests (donation_id, recipient_id, quantity, status, created_at) VALUES (?,?,5,'rejected',?)",
            (d1_avail, r1, iso(-1)),
        )
        # r2 has 1 claim on Donor 2's private donation
        db.insert(
            "INSERT INTO requests (donation_id, recipient_id, quantity, status, created_at) VALUES (?,?,10,'accepted',?)",
            (d2_avail, r2, iso(-1)),
        )
        db.commit()

    def test_donor_dashboards_accurate_and_isolated(self):
        # Donor 1 sees all 4 of their donations, 3 incoming requests, and ZERO of Donor 2's records
        self._login("d1@example.com")
        resp = self.client.get("/my")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("D1 Rice", html)
        self.assertIn("D1 Rolls", html)
        self.assertIn("D1 Expired Fruit", html)
        self.assertIn("D1 Cancelled Snacks", html)
        self.assertIn("25 / 40", html)
        self.assertIn("0 / 20", html)
        self.assertIn("Recipient One NGO", html)

        # Summary KPI checks (4 donations, 1 available, 1 claimed, 1 expired, 1 cancelled, 85 donated, 25 open, 3 requests)
        self.assertIn('<span class="stat-value">4</span><span class="stat-label">Total donations</span>', html)
        self.assertIn('<span class="stat-value">85</span><span class="stat-label">Portions donated</span>', html)
        self.assertIn('<span class="stat-value">25</span><span class="stat-label">Portions open</span>', html)
        self.assertIn('<span class="stat-value">3</span><span class="stat-label">Requests received</span>', html)

        # Must NOT see Donor 2 or Recipient 2 private activity
        self.assertNotIn("D2 Secret Milk", html)
        self.assertNotIn("Recipient Two Shelter", html)

        # Status filter works on My listings table
        exp_only = self.client.get("/my?status=expired").get_data(as_text=True)
        listings_section = exp_only.split("Claims &amp; requests received")[0]
        self.assertIn("D1 Expired Fruit", listings_section)
        self.assertNotIn("D1 Rice", listings_section)
        self._logout()

        # Donor 2 sees ONLY D2 Secret Milk and Recipient Two Shelter
        self._login("d2@example.com")
        d2_html = self.client.get("/my").get_data(as_text=True)
        self.assertIn("D2 Secret Milk", d2_html)
        self.assertIn("Recipient Two Shelter", d2_html)
        self.assertNotIn("D1 Rice", d2_html)
        self.assertNotIn("Recipient One NGO", d2_html)

    def test_receiver_dashboards_accurate_and_isolated(self):
        # Recipient 1 sees 3 claims (1 active, 1 completed, 1 cancelled/rejected, 35 active/completed portions)
        self._login("r1@example.com")
        html = self.client.get("/my").get_data(as_text=True)
        self.assertIn("D1 Rice", html)
        self.assertIn("D1 Rolls", html)
        self.assertIn("Donor One Cafe", html)
        self.assertIn("Loc 1", html)

        self.assertIn('<span class="stat-value">3</span><span class="stat-label">Claims submitted</span>', html)
        self.assertIn('<span class="stat-value">1</span><span class="stat-label">Completed claims</span>', html)
        self.assertIn('<span class="stat-value">1</span><span class="stat-label">Cancelled / Rejected</span>', html)
        self.assertIn('<span class="stat-value">35</span><span class="stat-label">Portions claimed</span>', html)

        # Must NOT see Recipient 2's reservation on D2 Secret Milk
        self.assertNotIn("D2 Secret Milk", html)
        self.assertNotIn("Donor Two Bakery", html)

        # Filter by completed
        comp_html = self.client.get("/my?status=completed").get_data(as_text=True)
        self.assertIn("D1 Rolls", comp_html)
        self.assertNotIn("D1 Rice", comp_html)
        self._logout()

        # Recipient 2 sees ONLY D2 Secret Milk
        self._login("r2@example.com")
        r2_html = self.client.get("/my").get_data(as_text=True)
        self.assertIn("D2 Secret Milk", r2_html)
        self.assertNotIn("D1 Rice", r2_html)
        self.assertNotIn("D1 Rolls", r2_html)

    def test_empty_states_for_new_donor_and_receiver(self):
        self._login("ed@example.com")
        ed_html = self.client.get("/my").get_data(as_text=True)
        self.assertIn("You have not created any food donations yet.", ed_html)
        self.assertIn("Create a donation", ed_html)
        self._logout()

        self._login("er@example.com")
        er_html = self.client.get("/my").get_data(as_text=True)
        self.assertIn("You have not claimed any food donations yet.", er_html)
        self.assertIn("Browse available donations", er_html)


if __name__ == "__main__":
    unittest.main()
