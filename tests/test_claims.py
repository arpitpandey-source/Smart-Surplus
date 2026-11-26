"""Phase 5 Food Claim / Request Workflow tests for SmartSurplus.

Covers:
- Claim 1 portion, multiple portions, and all remaining portions
- Claim more than available, zero, negative, expired, cancelled, and own donation
- Duplicate/accidental double-submission blocked by single-use claim_token
- Concurrent multi-thread claims prevent overselling
- Transaction rollback restores remaining_quantity & status when an error occurs
- Request status lifecycle (pending, accepted, rejected, completed, cancelled) + stock restoration
- Non-destructive SQLite migration of requests table preserving rows and converting 'collected' -> 'completed'

Run with:
    .venv/bin/python -m unittest discover -s tests -v
"""
import os
import re
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from werkzeug.security import generate_password_hash

os.environ.pop("AZURE_SQL_CONNECTION_STRING", None)

import app as app_mod
import db


class SmartSurplusClaimTests(unittest.TestCase):
    """Comprehensive tests for claim concurrency, rollbacks, validation, and request lifecycle."""

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
    def _extract(resp, field_name):
        html = resp.get_data(as_text=True)
        match = re.search(rf'name="{field_name}"\s+value="([^"]+)"', html)
        return match.group(1) if match else ""

    def _create_user(self, full_name, email, password, role):
        with app_mod.app.app_context():
            uid = db.insert(
                "INSERT INTO users (full_name, email, password_hash, role, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (full_name, email, generate_password_hash(password), role, app_mod.now_iso()),
            )
            db.commit()
            return uid

    def _create_donation(self, donor_id, food_name="Meals", qty=20, hours=8, status="available"):
        with app_mod.app.app_context():
            dl = (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds")
            did = db.insert(
                "INSERT INTO donations (donor_id, category_id, food_name, description, "
                "quantity, remaining_quantity, location, deadline, status, created_at) "
                "VALUES (?, 1, ?, 'Fresh food', ?, ?, 'Main Hub', ?, ?, ?)",
                (donor_id, food_name, qty, qty, dl, status, app_mod.now_iso()),
            )
            db.commit()
            return did

    def _login_client(self, client, email, password):
        csrf = self._extract(client.get("/login"), "csrf_token")
        return client.post(
            "/login",
            data={"csrf_token": csrf, "email": email, "password": password},
            follow_redirects=True,
        )

    # ----------------------------------------------------------- Quantity cases
    def test_claim_one_multiple_and_all_remaining_portions(self):
        donor_id = self._create_user("Donor", "d@example.com", "Strong@123", "donor")
        self._create_user("NGO", "r@example.com", "Strong@123", "recipient")
        did = self._create_donation(donor_id, qty=10)

        self._login_client(self.client, "r@example.com", "Strong@123")

        # Claim 1 portion
        detail = self.client.get(f"/donations/{did}")
        r1 = self.client.post(
            f"/donations/{did}/claim",
            data={
                "csrf_token": self._extract(detail, "csrf_token"),
                "claim_token": self._extract(detail, "claim_token"),
                "quantity": "1",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Reserved 1 portion(s).", r1.data)

        # Claim multiple (4 portions)
        detail = self.client.get(f"/donations/{did}")
        r2 = self.client.post(
            f"/donations/{did}/claim",
            data={
                "csrf_token": self._extract(detail, "csrf_token"),
                "claim_token": self._extract(detail, "claim_token"),
                "quantity": "4",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Reserved 4 portion(s).", r2.data)

        # Claim all remaining (5 portions) -> status becomes 'claimed'
        detail = self.client.get(f"/donations/{did}")
        r3 = self.client.post(
            f"/donations/{did}/claim",
            data={
                "csrf_token": self._extract(detail, "csrf_token"),
                "claim_token": self._extract(detail, "claim_token"),
                "quantity": "5",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Reserved 5 portion(s).", r3.data)

        with app_mod.app.app_context():
            d = db.query_one("SELECT remaining_quantity, status FROM donations WHERE id = ?", (did,))
            self.assertEqual(d["remaining_quantity"], 0)
            self.assertEqual(d["status"], "claimed")
            reqs = db.query("SELECT quantity, status FROM requests WHERE donation_id = ? ORDER BY id", (did,))
            self.assertEqual([(x["quantity"], x["status"]) for x in reqs], [(1, "pending"), (4, "pending"), (5, "pending")])

    def test_invalid_claim_cases_over_zero_negative_expired_cancelled(self):
        donor_id = self._create_user("Donor", "d@example.com", "Strong@123", "donor")
        self._create_user("NGO", "r@example.com", "Strong@123", "recipient")
        did_open = self._create_donation(donor_id, "Open Food", qty=5, hours=6)
        did_expired = self._create_donation(donor_id, "Expired Food", qty=5, hours=-3)
        did_cancelled = self._create_donation(donor_id, "Cancelled Food", qty=5, hours=6, status="cancelled")

        self._login_client(self.client, "r@example.com", "Strong@123")
        csrf = self._extract(self.client.get("/donations"), "csrf_token")

        cases = [
            (did_open, "6", b"no longer available"),
            (did_open, "0", b"Enter a valid number of portions."),
            (did_open, "-3", b"Enter a valid number of portions."),
            (did_expired, "1", b"expired"),
            (did_cancelled, "1", b"cancelled"),
        ]
        for target_id, qty, expected in cases:
            with self.subTest(target_id=target_id, qty=qty):
                resp = self.client.post(
                    f"/donations/{target_id}/claim",
                    data={"csrf_token": csrf, "quantity": qty},
                    follow_redirects=True,
                )
                self.assertIn(expected, resp.data)

        with app_mod.app.app_context():
            for did in (did_open, did_expired, did_cancelled):
                row = db.query_one("SELECT remaining_quantity FROM donations WHERE id = ?", (did,))
                self.assertEqual(row["remaining_quantity"], 5)
            self.assertEqual(db.query_one("SELECT COUNT(*) AS c FROM requests")["c"], 0)

    def test_cannot_claim_own_donation(self):
        # Even if a user owns a donation and has recipient role (e.g., role change), self-claim is blocked
        uid = self._create_user("Dual User", "self@example.com", "Strong@123", "recipient")
        did = self._create_donation(uid, "My Own Food", qty=10, hours=6)

        self._login_client(self.client, "self@example.com", "Strong@123")
        csrf = self._extract(self.client.get("/donations"), "csrf_token")
        resp = self.client.post(
            f"/donations/{did}/claim",
            data={"csrf_token": csrf, "quantity": "2"},
            follow_redirects=True,
        )
        self.assertIn(b"You cannot claim your own donation.", resp.data)
        with app_mod.app.app_context():
            self.assertEqual(db.query_one("SELECT remaining_quantity FROM donations WHERE id = ?", (did,))["remaining_quantity"], 10)

    def test_duplicate_claim_submission_blocked_by_claim_token(self):
        donor_id = self._create_user("Donor", "d@example.com", "Strong@123", "donor")
        self._create_user("NGO", "r@example.com", "Strong@123", "recipient")
        did = self._create_donation(donor_id, qty=10)

        self._login_client(self.client, "r@example.com", "Strong@123")
        page = self.client.get(f"/donations/{did}")
        csrf = self._extract(page, "csrf_token")
        ctok = self._extract(page, "claim_token")

        first = self.client.post(
            f"/donations/{did}/claim",
            data={"csrf_token": csrf, "claim_token": ctok, "quantity": "3"},
            follow_redirects=True,
        )
        self.assertIn(b"Reserved 3 portion(s).", first.data)

        # Exact same form token replayed (double-click / browser refresh) is rejected
        replay = self.client.post(
            f"/donations/{did}/claim",
            data={"csrf_token": csrf, "claim_token": ctok, "quantity": "3"},
            follow_redirects=True,
        )
        self.assertIn(b"already submitted", replay.data)

        with app_mod.app.app_context():
            self.assertEqual(db.query_one("SELECT remaining_quantity FROM donations WHERE id = ?", (did,))["remaining_quantity"], 7)
            self.assertEqual(db.query_one("SELECT COUNT(*) AS c FROM requests")["c"], 1)

    # ------------------------------------------------- Concurrency & Rollback
    def test_concurrent_claims_never_oversell(self):
        donor_id = self._create_user("Donor", "d@example.com", "Strong@123", "donor")
        self._create_user("NGO 1", "r1@example.com", "Strong@123", "recipient")
        self._create_user("NGO 2", "r2@example.com", "Strong@123", "recipient")
        did = self._create_donation(donor_id, qty=5)

        c1 = app_mod.app.test_client()
        c2 = app_mod.app.test_client()
        self._login_client(c1, "r1@example.com", "Strong@123")
        self._login_client(c2, "r2@example.com", "Strong@123")
        csrf1 = self._extract(c1.get("/donations"), "csrf_token")
        csrf2 = self._extract(c2.get("/donations"), "csrf_token")

        barrier = threading.Barrier(2)
        results = []
        lock = threading.Lock()

        def worker(client, csrf):
            barrier.wait()
            resp = client.post(
                f"/donations/{did}/claim",
                data={"csrf_token": csrf, "quantity": "4"},
                follow_redirects=True,
            )
            with lock:
                results.append(resp.get_data(as_text=True))

        t1 = threading.Thread(target=worker, args=(c1, csrf1))
        t2 = threading.Thread(target=worker, args=(c2, csrf2))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        successes = sum("Reserved 4 portion(s)." in r for r in results)
        failures = sum("no longer available" in r for r in results)
        self.assertEqual(successes, 1)
        self.assertEqual(failures, 1)

        with app_mod.app.app_context():
            d = db.query_one("SELECT remaining_quantity, status FROM donations WHERE id = ?", (did,))
            self.assertEqual(d["remaining_quantity"], 1)
            self.assertEqual(d["status"], "available")
            self.assertEqual(db.query_one("SELECT COUNT(*) AS c FROM requests")["c"], 1)

    def test_transaction_rollback_on_mid_claim_failure(self):
        donor_id = self._create_user("Donor", "d@example.com", "Strong@123", "donor")
        self._create_user("NGO", "r@example.com", "Strong@123", "recipient")
        did = self._create_donation(donor_id, qty=5)

        self._login_client(self.client, "r@example.com", "Strong@123")
        csrf = self._extract(self.client.get("/donations"), "csrf_token")

        # Simulate crash right after UPDATE donations (during INSERT INTO requests)
        with patch("db.insert", side_effect=RuntimeError("Simulated DB crash")):
            resp = self.client.post(
                f"/donations/{did}/claim",
                data={"csrf_token": csrf, "quantity": "5"},
                follow_redirects=True,
            )
        self.assertIn(b"Could not complete your request.", resp.data)

        # Verify UPDATE donations (remaining_quantity=0 and status='claimed') rolled back cleanly
        with app_mod.app.app_context():
            d = db.query_one("SELECT remaining_quantity, status FROM donations WHERE id = ?", (did,))
            self.assertEqual(d["remaining_quantity"], 5)
            self.assertEqual(d["status"], "available")
            self.assertEqual(db.query_one("SELECT COUNT(*) AS c FROM requests")["c"], 0)

    # ---------------------------------------------- Request Status Transitions
    def test_request_lifecycle_accept_reject_complete_cancel(self):
        donor_id = self._create_user("Donor", "d@example.com", "Strong@123", "donor")
        self._create_user("NGO", "r@example.com", "Strong@123", "recipient")
        self._create_user("Other Donor", "od@example.com", "Strong@123", "donor")
        did = self._create_donation(donor_id, qty=10)

        # Recipient submits 3 claims (4, 3, 3 -> total 10, donation becomes 'claimed')
        self._login_client(self.client, "r@example.com", "Strong@123")
        csrf = self._extract(self.client.get("/donations"), "csrf_token")
        self.client.post(f"/donations/{did}/claim", data={"csrf_token": csrf, "quantity": "4"})
        self.client.post(f"/donations/{did}/claim", data={"csrf_token": csrf, "quantity": "3"})
        self.client.post(f"/donations/{did}/claim", data={"csrf_token": csrf, "quantity": "3"})

        # Recipient cancels request #1 (4 portions) -> stock restored to 4 & donation reopened
        csrf_my = self._extract(self.client.get("/my"), "csrf_token")
        cancel_resp = self.client.post(
            "/requests/1/cancel",
            data={"csrf_token": csrf_my, "next": "my"},
            follow_redirects=True,
        )
        self.assertIn(b"Request #1 cancelled and 4 portion(s) released.", cancel_resp.data)
        with app_mod.app.app_context():
            d = db.query_one("SELECT remaining_quantity, status FROM donations WHERE id = ?", (did,))
            self.assertEqual((d["remaining_quantity"], d["status"]), (4, "available"))

        # Recipient tries to accept own request -> 403
        self.assertEqual(
            self.client.post("/requests/2/accept", data={"csrf_token": csrf_my}).status_code,
            403,
        )
        self.client.post("/logout", data={"csrf_token": csrf_my})

        # Unrelated donor cannot accept/reject/complete -> 403
        self._login_client(self.client, "od@example.com", "Strong@123")
        csrf_od = self._extract(self.client.get("/my"), "csrf_token")
        self.assertEqual(self.client.post("/requests/2/accept", data={"csrf_token": csrf_od}).status_code, 403)
        self.assertEqual(self.client.post("/requests/2/reject", data={"csrf_token": csrf_od}).status_code, 403)
        self.assertEqual(self.client.post("/requests/2/complete", data={"csrf_token": csrf_od}).status_code, 403)
        self.client.post("/logout", data={"csrf_token": csrf_od})

        # Owner donor accepts #2 -> completes #2 -> rejects #3 (restoring 3 more portions)
        self._login_client(self.client, "d@example.com", "Strong@123")
        csrf_d = self._extract(self.client.get("/my"), "csrf_token")
        self.assertIn(b"Request #2 accepted.", self.client.post("/requests/2/accept", data={"csrf_token": csrf_d}, follow_redirects=True).data)
        self.assertIn(b"Request #2 marked completed.", self.client.post("/requests/2/complete", data={"csrf_token": csrf_d}, follow_redirects=True).data)
        self.assertIn(b"Request #3 rejected and 3 portion(s) restored.", self.client.post("/requests/3/reject", data={"csrf_token": csrf_d}, follow_redirects=True).data)

        with app_mod.app.app_context():
            d = db.query_one("SELECT remaining_quantity, status FROM donations WHERE id = ?", (did,))
            self.assertEqual((d["remaining_quantity"], d["status"]), (7, "available"))
            statuses = {r["id"]: r["status"] for r in db.query("SELECT id, status FROM requests")}
            self.assertEqual(statuses, {1: "cancelled", 2: "completed", 3: "rejected"})

    def test_requests_schema_migration_preserves_data_and_maps_collected(self):
        legacy_dir = tempfile.TemporaryDirectory()
        legacy_db = os.path.join(legacy_dir.name, "smartsurplus_v2.db")
        conn = sqlite3.connect(legacy_db)
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
                role TEXT NOT NULL, failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
            INSERT INTO categories (name) VALUES ('Bakery');
            CREATE TABLE donations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, donor_id INTEGER NOT NULL REFERENCES users(id),
                category_id INTEGER NOT NULL REFERENCES categories(id), food_name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', quantity INTEGER NOT NULL,
                remaining_quantity INTEGER NOT NULL, location TEXT NOT NULL,
                deadline TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'available', created_at TEXT NOT NULL
            );
            CREATE TABLE requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                donation_id INTEGER NOT NULL REFERENCES donations(id),
                recipient_id INTEGER NOT NULL REFERENCES users(id),
                quantity INTEGER NOT NULL CHECK (quantity >= 1),
                status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'collected', 'cancelled')),
                created_at TEXT NOT NULL
            );
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NULL,
                action TEXT NOT NULL, detail TEXT NULL, ip_address TEXT NULL, created_at TEXT NOT NULL
            );
            CREATE VIEW v_open_donations AS
            SELECT d.id, d.donor_id, d.food_name, d.description, d.quantity, d.remaining_quantity,
                   d.location, d.deadline, d.created_at, c.name AS category, u.full_name AS donor_name
            FROM donations d JOIN categories c ON c.id = d.category_id JOIN users u ON u.id = d.donor_id
            WHERE d.status = 'available' AND d.remaining_quantity > 0;
            INSERT INTO users (full_name, email, password_hash, role, created_at) VALUES
                ('D', 'd@x.com', 'h', 'donor', '2026-01-01T10:00:00'),
                ('R', 'r@x.com', 'h', 'recipient', '2026-01-01T10:00:00');
            INSERT INTO donations (donor_id, category_id, food_name, quantity, remaining_quantity, location, deadline, status, created_at)
            VALUES (1, 1, 'Bread', 10, 5, 'X', '2099-01-01T10:00:00', 'available', '2026-01-01T10:00:00');
            INSERT INTO requests (donation_id, recipient_id, quantity, status, created_at)
            VALUES (1, 2, 5, 'collected', '2026-01-01T10:05:00');
            """
        )
        conn.commit()
        conn.close()

        saved_instance = app_mod.app.instance_path
        try:
            app_mod.app.instance_path = legacy_dir.name
            with app_mod.app.app_context():
                db.init_db()
                req = db.query_one("SELECT quantity, status FROM requests WHERE id = 1")
                user = db.query_one("SELECT is_active FROM users WHERE id = 1")
                fk_issues = db.get_db().execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(req["quantity"], 5)
            self.assertEqual(req["status"], "completed")
            self.assertEqual(user["is_active"], 1)
            self.assertEqual(fk_issues, [])
        finally:
            app_mod.app.instance_path = saved_instance
            legacy_dir.cleanup()


if __name__ == "__main__":
    unittest.main()

