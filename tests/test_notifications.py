"""Phase 9 Persistent In-App Notification System tests for SmartSurplus.

Covers:
- Claim received, claim confirmed, and fully claimed notifications
- Request status change notifications (accepted, rejected, completed, cancelled)
- Donation cancelled notifications (to affected recipients and donor when admin cancels)
- Approaching deadline (expiring soon) & expired notifications with strict duplicate prevention
- Failed DB operations (rolled-back claim) create ZERO notifications
- Notification access control (users only see their own, cannot mark another user's as read)
- Unread badge count, mark single read, mark all read, unread filter, and history

Run with:
    .venv/bin/python -m unittest discover -s tests -v
"""
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from werkzeug.security import generate_password_hash

os.environ.pop("AZURE_SQL_CONNECTION_STRING", None)

import app as app_mod
import db


class SmartSurplusNotificationTests(unittest.TestCase):
    """Test notification creation, transactions, dedup, reading, and access control."""

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
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.get_data(as_text=True))
        return match.group(1) if match else ""

    def _create_user(self, name, email, role):
        with app_mod.app.app_context():
            uid = db.insert(
                "INSERT INTO users (full_name, email, password_hash, role, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, email, generate_password_hash("Strong@123"), role, app_mod.now_iso()),
            )
            db.commit()
            return uid

    def _create_donation(self, donor_id, food_name="Meals", qty=10, hours=12):
        with app_mod.app.app_context():
            dl = (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds")
            did = db.insert(
                "INSERT INTO donations (donor_id, category_id, food_name, description, "
                "quantity, remaining_quantity, location, deadline, status, created_at) "
                "VALUES (?, 1, ?, '', ?, ?, 'Hub', ?, 'available', ?)",
                (donor_id, food_name, qty, qty, dl, app_mod.now_iso()),
            )
            db.commit()
            return did

    def _login(self, email):
        csrf = self._csrf(self.client.get("/login"))
        return self.client.post(
            "/login",
            data={"csrf_token": csrf, "email": email, "password": "Strong@123"},
            follow_redirects=True,
        )

    def _logout(self):
        csrf = self._csrf(self.client.get("/donations"))
        self.client.post("/logout", data={"csrf_token": csrf})

    # ----------------------------------------------------------- Event Creation
    def test_claim_full_claim_and_request_status_notifications(self):
        d_id = self._create_user("Cafe Donor", "d@example.com", "donor")
        r_id = self._create_user("NGO One", "r@example.com", "recipient")
        did = self._create_donation(d_id, "Warm Biryani", qty=10, hours=12)

        # Partial claim (4) + full claim (6)
        self._login("r@example.com")
        csrf = self._csrf(self.client.get("/donations"))
        self.client.post(f"/donations/{did}/claim", data={"csrf_token": csrf, "quantity": "4"})
        self.client.post(f"/donations/{did}/claim", data={"csrf_token": csrf, "quantity": "6"})

        # Check recipient unread count (2 CLAIM_CONFIRMED) and mark #1 read
        rec_page = self.client.get("/notifications").get_data(as_text=True)
        self.assertIn("2 unread", rec_page)
        self.assertIn("CLAIM_CONFIRMED", rec_page)
        self.assertIn("Warm Biryani", rec_page)
        self._logout()

        # Donor sees 2 CLAIM_RECEIVED + 1 DONATION_FULLY_CLAIMED = 3 unread
        self._login("d@example.com")
        donor_page = self.client.get("/notifications").get_data(as_text=True)
        self.assertIn("3 unread", donor_page)
        self.assertIn("CLAIM_RECEIVED", donor_page)
        self.assertIn("DONATION_FULLY_CLAIMED", donor_page)
        self.assertIn('<span class="nav-badge">3</span>', donor_page)

        # Donor accepts request #1 and rejects request #2 -> recipient gets REQUEST_ACCEPTED & REQUEST_REJECTED
        csrf_d = self._csrf(self.client.get("/my"))
        self.client.post("/requests/1/accept", data={"csrf_token": csrf_d})
        self.client.post("/requests/2/reject", data={"csrf_token": csrf_d})
        self.client.post("/requests/1/complete", data={"csrf_token": csrf_d})
        self._logout()

        self._login("r@example.com")
        rec_html = self.client.get("/notifications").get_data(as_text=True)
        self.assertIn("REQUEST_ACCEPTED", rec_html)
        self.assertIn("REQUEST_REJECTED", rec_html)
        self.assertIn("REQUEST_COMPLETED", rec_html)
        self.assertIn("5 unread", rec_html)

        with app_mod.app.app_context():
            # Verify recipient never sees donor's notifications
            donor_only = db.query("SELECT id FROM notifications WHERE user_id = ? AND event_type = 'DONATION_FULLY_CLAIMED'", (d_id,))
            self.assertEqual(len(donor_only), 1)
            self.assertNotIn("DONATION_FULLY_CLAIMED", rec_html)

    def test_donation_cancelled_notifies_affected_recipients(self):
        d_id = self._create_user("Donor", "d@example.com", "donor")
        self._create_user("NGO 1", "r1@example.com", "recipient")
        self._create_user("Admin", "a@example.com", "admin")
        did = self._create_donation(d_id, "Fruit Trays", qty=10, hours=12)

        self._login("r1@example.com")
        csrf_r = self._csrf(self.client.get("/donations"))
        self.client.post(f"/donations/{did}/claim", data={"csrf_token": csrf_r, "quantity": "3"})
        self._logout()

        # Admin cancels the donation -> notifies BOTH recipient and donor
        self._login("a@example.com")
        csrf_a = self._csrf(self.client.get("/admin/donations"))
        self.client.post(f"/donations/{did}/cancel", data={"csrf_token": csrf_a})
        self._logout()

        self._login("r1@example.com")
        r_html = self.client.get("/notifications").get_data(as_text=True)
        self.assertIn("DONATION_CANCELLED", r_html)
        self.assertIn("Fruit Trays", r_html)
        self._logout()

        self._login("d@example.com")
        d_html = self.client.get("/notifications").get_data(as_text=True)
        self.assertIn("DONATION_CANCELLED", d_html)
        self.assertIn("cancelled by an administrator", d_html)

    # ------------------------------------------------- Expiry & Deduplication
    def test_approaching_and_expired_notifications_do_not_duplicate(self):
        d_id = self._create_user("Donor", "d@example.com", "donor")
        r_id = self._create_user("NGO", "r@example.com", "recipient")
        # Expiring in 2 hours (within 6h window) and already expired (-1h)
        did_soon = self._create_donation(d_id, "Soon Bread", qty=10, hours=2)
        did_exp = self._create_donation(d_id, "Expired Soup", qty=8, hours=-1)

        with app_mod.app.app_context():
            db.insert(
                "INSERT INTO requests (donation_id, recipient_id, quantity, status, created_at) "
                "VALUES (?, ?, 2, 'pending', ?)",
                (did_soon, r_id, app_mod.now_iso()),
            )
            db.commit()

        # Donor visits multiple pages -> created ONCE only (no duplicate expiry notifications!)
        self._login("d@example.com")
        self.client.get("/")
        self.client.get("/my")
        self.client.get("/notifications")
        donor_html = self.client.get("/notifications").get_data(as_text=True)
        self.assertIn("DONATION_EXPIRING_SOON", donor_html)
        self.assertIn("DONATION_EXPIRED", donor_html)
        self.assertEqual(donor_html.count("Soon Bread"), 1)
        self.assertEqual(donor_html.count("Expired Soup"), 1)
        self._logout()

        # Recipient visits multiple pages -> approaching reminder created ONCE only
        self._login("r@example.com")
        self.client.get("/my")
        self.client.get("/notifications")
        rec_html = self.client.get("/notifications").get_data(as_text=True)
        self.assertIn("DONATION_EXPIRING_SOON", rec_html)
        self.assertEqual(rec_html.count("Soon Bread"), 1)

    # -------------------------------------- Rollback, Access Control & Reading
    def test_failed_db_operation_creates_zero_notifications(self):
        d_id = self._create_user("Donor", "d@example.com", "donor")
        self._create_user("NGO", "r@example.com", "recipient")
        did = self._create_donation(d_id, "Meals", qty=5, hours=12)

        self._login("r@example.com")
        csrf = self._csrf(self.client.get("/donations"))
        with patch("db.insert", side_effect=RuntimeError("Simulated failure")):
            resp = self.client.post(
                f"/donations/{did}/claim",
                data={"csrf_token": csrf, "quantity": "5"},
                follow_redirects=True,
            )
        self.assertIn(b"Could not complete your request.", resp.data)

        with app_mod.app.app_context():
            self.assertEqual(db.query_one("SELECT COUNT(*) AS c FROM notifications")["c"], 0)

    def test_mark_read_mark_all_read_and_access_control(self):
        d_id = self._create_user("Donor", "d@example.com", "donor")
        self._create_user("NGO", "r@example.com", "recipient")
        did = self._create_donation(d_id, "Pasta", qty=10, hours=12)

        # Anonymous cannot view or mark notifications
        self.assertEqual(self.client.get("/notifications").status_code, 302)

        self._login("r@example.com")
        csrf_r = self._csrf(self.client.get("/donations"))
        self.client.post(f"/donations/{did}/claim", data={"csrf_token": csrf_r, "quantity": "2"})
        self.client.post(f"/donations/{did}/claim", data={"csrf_token": csrf_r, "quantity": "2"})

        with app_mod.app.app_context():
            donor_notif = db.query_one("SELECT id FROM notifications WHERE user_id = ?", (d_id,))
            rec_notifs = db.query("SELECT id FROM notifications WHERE user_id != ? ORDER BY id", (d_id,))

        # Recipient CANNOT mark Donor's notification as read -> 403
        csrf_n = self._csrf(self.client.get("/notifications"))
        forbidden = self.client.post(
            f"/notifications/{donor_notif['id']}/read",
            data={"csrf_token": csrf_n},
        )
        self.assertEqual(forbidden.status_code, 403)

        # Recipient marks first notification as read -> unread drops from 2 to 1
        self.client.post(
            f"/notifications/{rec_notifs[0]['id']}/read",
            data={"csrf_token": csrf_n},
            follow_redirects=True,
        )
        unread_view = self.client.get("/notifications?filter=unread").get_data(as_text=True)
        self.assertIn("1 unread", unread_view)
        self.assertIn("1 total in this view", unread_view)

        # Mark all as read -> 0 unread, history still visible under All
        mark_all = self.client.post(
            "/notifications/read-all",
            data={"csrf_token": csrf_n},
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn("All notifications marked as read.", mark_all)
        self.assertIn("0 unread", mark_all)
        self.assertIn("2 total in this view", mark_all)


if __name__ == "__main__":
    unittest.main()

