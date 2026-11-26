"""Phase 3 Food Donation Management & Migration tests for SmartSurplus.

Covers:
- Create donation (including description)
- Invalid quantity, invalid deadline, invalid category, missing fields
- View donor donations (/my) and donation detail page (/donations/<id>)
- Remaining quantity tracking and effective status display
- Edit donation (including quantity adjustments with existing claims)
- Unauthorized edit/cancel/delete (403 / 302)
- Cancel donation (cancels pending requests) and delete integrity protection
- Expired donations cannot be claimed
- Safe non-destructive SQLite migration preserving existing data
- Persistence across Flask restart simulation

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

os.environ.pop("AZURE_SQL_CONNECTION_STRING", None)

import app as app_mod
import db


class SmartSurplusDonationTests(unittest.TestCase):
    """Comprehensive tests for donation CRUD, authorization, integrity, and migrations."""

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

    def _create_user(self, full_name, email, password, role):
        with app_mod.app.app_context():
            uid = db.insert(
                "INSERT INTO users (full_name, email, password_hash, role, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (full_name, email, generate_password_hash(password), role, app_mod.now_iso()),
            )
            db.commit()
            return uid

    def _login(self, email, password):
        csrf = self._csrf(self.client.get("/login"))
        return self.client.post(
            "/login",
            data={"csrf_token": csrf, "email": email, "password": password},
            follow_redirects=True,
        )

    def _logout(self):
        csrf = self._csrf(self.client.get("/donations"))
        return self.client.post("/logout", data={"csrf_token": csrf}, follow_redirects=True)

    @staticmethod
    def _future_str(hours=6):
        return (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")

    def _create_donation_via_ui(self, food_name="Pasta Trays", qty="20", cat="1",
                                location="Green Street", desc="Fresh veg pasta", hours=6):
        csrf = self._csrf(self.client.get("/donate"))
        return self.client.post(
            "/donate",
            data={
                "csrf_token": csrf,
                "food_name": food_name,
                "category_id": cat,
                "description": desc,
                "quantity": qty,
                "location": location,
                "deadline": self._future_str(hours),
            },
            follow_redirects=True,
        )

    # ----------------------------------------------------------- Create & Validate
    def test_create_donation_and_view_details_and_my_listings(self):
        self._create_user("Cafe Donor", "donor@example.com", "Strong@123", "donor")
        self._login("donor@example.com", "Strong@123")

        resp = self._create_donation_via_ui(
            food_name="Fresh Biryani",
            qty="30",
            cat="1",
            location="MG Road",
            desc="Packed in insulated containers",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Donation #1 saved successfully!", resp.data)
        self.assertIn(b"Fresh Biryani", resp.data)
        self.assertIn(b"Packed in insulated containers", resp.data)

        # Detail page displays all fields
        detail = self.client.get("/donations/1")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Fresh Biryani", detail.data)
        self.assertIn(b"Cooked meals", detail.data)
        self.assertIn(b"Packed in insulated containers", detail.data)
        self.assertIn(b"30 of 30", detail.data)
        self.assertIn(b"MG Road", detail.data)
        self.assertIn(b"available", detail.data)

        # Donor dashboard (/my) shows listing, remaining qty, and status
        my_page = self.client.get("/my")
        self.assertEqual(my_page.status_code, 200)
        self.assertIn(b"Fresh Biryani", my_page.data)
        self.assertIn(b"30 / 30", my_page.data)
        self.assertIn(b"available", my_page.data)

    def test_create_donation_validation_errors(self):
        self._create_user("Cafe Donor", "donor@example.com", "Strong@123", "donor")
        self._login("donor@example.com", "Strong@123")

        past = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
        future = self._future_str(6)
        invalid_cases = [
            ("", "1", "10", "Loc", future, "ok", b"required fields"),
            ("Bread", "1", "10", "", future, "ok", b"required fields"),
            ("Bread", "1", "0", "Loc", future, "ok", b"between 1 and 100000"),
            ("Bread", "1", "-5", "Loc", future, "ok", b"between 1 and 100000"),
            ("Bread", "1", "100001", "Loc", future, "ok", b"between 1 and 100000"),
            ("Bread", "1", "abc", "Loc", future, "ok", b"valid quantity"),
            ("Bread", "1", "10", "Loc", past, "ok", b"must be in the future"),
            ("Bread", "1", "10", "Loc", "not-a-date", "ok", b"valid collection deadline"),
            ("Bread", "999", "10", "Loc", future, "ok", b"valid category"),
            ("Bread", "xyz", "10", "Loc", future, "ok", b"valid quantity"),
            ("Bread", "1", "10", "Loc", future, "D" * 501, b"500 characters"),
        ]
        for food, cat, qty, loc, dl, desc, snippet in invalid_cases:
            with self.subTest(food=food, cat=cat, qty=qty, dl=dl, desc_len=len(desc)):
                csrf = self._csrf(self.client.get("/donate"))
                resp = self.client.post(
                    "/donate",
                    data={
                        "csrf_token": csrf,
                        "food_name": food,
                        "category_id": cat,
                        "description": desc,
                        "quantity": qty,
                        "location": loc,
                        "deadline": dl,
                    },
                )
                self.assertEqual(resp.status_code, 200)
                self.assertIn(snippet, resp.data)

        with app_mod.app.app_context():
            self.assertEqual(db.query_one("SELECT COUNT(*) AS c FROM donations")["c"], 0)

    # ----------------------------------------------------------- Edit & Authz
    def test_edit_donation_adjusts_remaining_quantity_and_status(self):
        self._create_user("Donor A", "da@example.com", "Strong@123", "donor")
        self._create_user("NGO A", "na@example.com", "Strong@123", "recipient")

        self._login("da@example.com", "Strong@123")
        self._create_donation_via_ui(food_name="Sandwiches", qty="20", cat="2", location="Old Town")
        self._logout()

        # Recipient claims 12 portions (remaining becomes 8)
        self._login("na@example.com", "Strong@123")
        csrf = self._csrf(self.client.get("/donations/1"))
        self.client.post("/donations/1/claim", data={"csrf_token": csrf, "quantity": "12"})
        self._logout()

        # Donor attempts to reduce total quantity below claimed 12 -> rejected
        self._login("da@example.com", "Strong@123")
        csrf = self._csrf(self.client.get("/donations/1/edit"))
        bad_qty = self.client.post(
            "/donations/1/edit",
            data={
                "csrf_token": csrf,
                "food_name": "Sandwiches",
                "category_id": "2",
                "description": "Updated desc",
                "quantity": "10",
                "location": "New Town",
                "deadline": self._future_str(10),
            },
        )
        self.assertIn(b"cannot be less than the 12 portion(s) already claimed", bad_qty.data)

        # Donor increases total quantity to 25 -> remaining should become 25 - 12 = 13
        ok_edit = self.client.post(
            "/donations/1/edit",
            data={
                "csrf_token": csrf,
                "food_name": "Club Sandwiches",
                "category_id": "2",
                "description": "Fresh veg club sandwiches",
                "quantity": "25",
                "location": "New Town",
                "deadline": self._future_str(10),
            },
            follow_redirects=True,
        )
        self.assertEqual(ok_edit.status_code, 200)
        self.assertIn(b"Donation #1 updated.", ok_edit.data)
        self.assertIn(b"Club Sandwiches", ok_edit.data)
        self.assertIn(b"13 of 25", ok_edit.data)
        self.assertIn(b"New Town", ok_edit.data)

    def test_unauthorized_edit_cancel_delete_blocked(self):
        self._create_user("Owner Donor", "owner@example.com", "Strong@123", "donor")
        self._create_user("Other Donor", "other@example.com", "Strong@123", "donor")
        self._create_user("NGO User", "ngo@example.com", "Strong@123", "recipient")
        self._create_user("Admin User", "admin@example.com", "Strong@123", "admin")

        self._login("owner@example.com", "Strong@123")
        self._create_donation_via_ui(food_name="Fruit Boxes", qty="10", cat="3", location="Hub")
        self._logout()

        # Anonymous redirected
        self.assertEqual(self.client.get("/donations/1/edit").status_code, 302)

        # Other donor forbidden (403) on GET edit, POST edit, POST cancel, POST delete
        self._login("other@example.com", "Strong@123")
        self.assertEqual(self.client.get("/donations/1/edit").status_code, 403)
        csrf = self._csrf(self.client.get("/donations"))
        self.assertEqual(
            self.client.post("/donations/1/edit", data={"csrf_token": csrf, "food_name": "Hack"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post("/donations/1/cancel", data={"csrf_token": csrf}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post("/donations/1/delete", data={"csrf_token": csrf}).status_code,
            403,
        )
        # Other donor cannot see request management actions on detail page
        detail_other = self.client.get("/donations/1")
        self.assertEqual(detail_other.status_code, 200)
        self.assertNotIn(b"Edit donation", detail_other.data)
        self.assertNotIn(b"Requests on this donation", detail_other.data)
        self._logout()

        # Recipient forbidden (403)
        self._login("ngo@example.com", "Strong@123")
        self.assertEqual(self.client.get("/donations/1/edit").status_code, 403)
        csrf = self._csrf(self.client.get("/donations"))
        self.assertEqual(self.client.post("/donations/1/cancel", data={"csrf_token": csrf}).status_code, 403)
        self._logout()

        # Admin CAN view management actions & edit
        self._login("admin@example.com", "Strong@123")
        self.assertEqual(self.client.get("/donations/1/edit").status_code, 200)

    # ------------------------------------------------- Cancel, Delete, Expired
    def test_cancel_and_delete_integrity_rules(self):
        self._create_user("Donor", "d@example.com", "Strong@123", "donor")
        self._create_user("NGO", "r@example.com", "Strong@123", "recipient")

        self._login("d@example.com", "Strong@123")
        self._create_donation_via_ui(food_name="Claimed Item", qty="10", cat="1", location="A")
        self._create_donation_via_ui(food_name="Unclaimed Item", qty="10", cat="2", location="B")
        self._logout()

        # Claim 4 portions of donation #1
        self._login("r@example.com", "Strong@123")
        csrf = self._csrf(self.client.get("/donations/1"))
        self.client.post("/donations/1/claim", data={"csrf_token": csrf, "quantity": "4"})
        self._logout()

        # Owner cannot DELETE donation #1 because it has a request
        self._login("d@example.com", "Strong@123")
        csrf = self._csrf(self.client.get("/donations/1"))
        del_blocked = self.client.post(
            "/donations/1/delete", data={"csrf_token": csrf}, follow_redirects=True
        )
        self.assertIn(b"Cannot delete a donation that has claims or requests", del_blocked.data)

        # Owner CAN CANCEL donation #1; pending request is also cancelled
        cancelled = self.client.post(
            "/donations/1/cancel", data={"csrf_token": csrf}, follow_redirects=True
        )
        self.assertIn(b"Donation #1 has been cancelled.", cancelled.data)
        self.assertNotIn(b"Claimed Item", self.client.get("/donations").data)
        # Cancelled donation cannot be edited
        edit_cancelled = self.client.get("/donations/1/edit", follow_redirects=True)
        self.assertIn(b"Cancelled donations cannot be edited.", edit_cancelled.data)

        with app_mod.app.app_context():
            req = db.query_one("SELECT status FROM requests WHERE donation_id = 1")
            self.assertEqual(req["status"], "cancelled")

        # Owner CAN DELETE donation #2 because it has zero requests
        csrf = self._csrf(self.client.get("/donations/2"))
        deleted = self.client.post(
            "/donations/2/delete", data={"csrf_token": csrf}, follow_redirects=True
        )
        self.assertIn(b"Donation #2 deleted.", deleted.data)
        self.assertEqual(self.client.get("/donations/2").status_code, 404)

    def test_expired_donations_are_not_claimable_and_show_expired(self):
        donor_id = self._create_user("Donor", "d@example.com", "Strong@123", "donor")
        self._create_user("NGO", "r@example.com", "Strong@123", "recipient")
        past_deadline = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
        with app_mod.app.app_context():
            did = db.insert(
                "INSERT INTO donations (donor_id, category_id, food_name, description, "
                "quantity, remaining_quantity, location, deadline, status, created_at) "
                "VALUES (?, 1, 'Past Meal', 'Should not be claimed', 10, 10, 'Center', ?, 'available', ?)",
                (donor_id, past_deadline, app_mod.now_iso()),
            )
            db.commit()

        self._login("r@example.com", "Strong@123")
        # Not shown in open listings
        self.assertNotIn(b"Past Meal", self.client.get("/donations").data)
        # Detail shows expired and no reserve button
        detail = self.client.get(f"/donations/{did}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"expired", detail.data)
        self.assertIn(b"cannot be claimed", detail.data)

        # Direct POST to claim fails and leaves remaining_quantity untouched
        csrf = self._csrf(self.client.get("/donations"))
        claim_resp = self.client.post(
            f"/donations/{did}/claim",
            data={"csrf_token": csrf, "quantity": "2"},
            follow_redirects=True,
        )
        self.assertIn(b"expired and can no longer be claimed", claim_resp.data)
        with app_mod.app.app_context():
            row = db.query_one("SELECT remaining_quantity FROM donations WHERE id = ?", (did,))
            self.assertEqual(row["remaining_quantity"], 10)

    # ------------------------------------------ Migration & Restart Persistence
    def test_safe_migration_preserves_existing_rows(self):
        legacy_dir = tempfile.TemporaryDirectory()
        legacy_db = os.path.join(legacy_dir.name, "smartsurplus_v2.db")
        # Build legacy Phase 1/2 schema WITHOUT description column
        conn = sqlite3.connect(legacy_db)
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('donor','recipient','admin')),
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
            INSERT INTO categories (name) VALUES ('Cooked meals');
            CREATE TABLE donations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                donor_id INTEGER NOT NULL REFERENCES users(id),
                category_id INTEGER NOT NULL REFERENCES categories(id),
                food_name TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 100000),
                remaining_quantity INTEGER NOT NULL CHECK (remaining_quantity >= 0),
                location TEXT NOT NULL,
                deadline TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'available',
                created_at TEXT NOT NULL
            );
            CREATE TABLE requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                donation_id INTEGER NOT NULL REFERENCES donations(id),
                recipient_id INTEGER NOT NULL REFERENCES users(id),
                quantity INTEGER NOT NULL CHECK (quantity >= 1),
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            );
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NULL REFERENCES users(id),
                action TEXT NOT NULL, detail TEXT NULL, ip_address TEXT NULL, created_at TEXT NOT NULL
            );
            CREATE VIEW v_open_donations AS
            SELECT d.id, d.food_name, d.quantity, d.remaining_quantity, d.location,
                   d.deadline, d.created_at, c.name AS category, u.full_name AS donor_name
            FROM donations d JOIN categories c ON c.id = d.category_id JOIN users u ON u.id = d.donor_id
            WHERE d.status = 'available' AND d.remaining_quantity > 0;
            INSERT INTO users (full_name, email, password_hash, role, created_at)
            VALUES ('Legacy Donor', 'legacy@example.com', 'hash', 'donor', '2026-01-01T10:00:00');
            INSERT INTO donations (donor_id, category_id, food_name, quantity, remaining_quantity, location, deadline, status, created_at)
            VALUES (1, 1, 'Legacy Curry', 25, 20, 'Old Road', '2099-01-01T10:00:00', 'available', '2026-01-01T10:00:00');
            """
        )
        conn.commit()
        conn.close()

        saved_instance = app_mod.app.instance_path
        try:
            app_mod.app.instance_path = legacy_dir.name
            with app_mod.app.app_context():
                db.init_db()
                # Run twice to confirm idempotency
                db.init_db()
                row = db.query_one("SELECT food_name, description, remaining_quantity FROM donations WHERE id = 1")
                view_row = db.query_one("SELECT id, donor_id, food_name, description FROM v_open_donations WHERE id = 1")
            self.assertEqual(row["food_name"], "Legacy Curry")
            self.assertEqual(row["description"], "")
            self.assertEqual(row["remaining_quantity"], 20)
            self.assertEqual(view_row["donor_id"], 1)
            self.assertEqual(view_row["description"], "")
        finally:
            app_mod.app.instance_path = saved_instance
            legacy_dir.cleanup()

    def test_donation_persists_after_restart_simulation(self):
        self._create_user("Persist Donor", "persist@example.com", "Strong@123", "donor")
        self._login("persist@example.com", "Strong@123")
        self._create_donation_via_ui(
            food_name="Persisted Loaves", qty="18", cat="2", location="Baker St", desc="Warm bread"
        )
        csrf = self._csrf(self.client.get("/donations/1/edit"))
        self.client.post(
            "/donations/1/edit",
            data={
                "csrf_token": csrf,
                "food_name": "Persisted Loaves Updated",
                "category_id": "2",
                "description": "Still warm",
                "quantity": "22",
                "location": "Baker St #2",
                "deadline": self._future_str(12),
            },
        )

        # Simulate Flask restart against the exact same instance_path
        with app_mod.app.app_context():
            db.close_db()
            db.init_db()

        fresh_client = app_mod.app.test_client()
        listing = fresh_client.get("/donations")
        self.assertEqual(listing.status_code, 200)
        self.assertIn(b"Persisted Loaves Updated", listing.data)
        self.assertIn(b"Still warm", listing.data)
        self.assertIn(b"22 of 22", listing.data)


if __name__ == "__main__":
    unittest.main()
