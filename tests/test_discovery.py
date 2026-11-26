"""Phase 4 Donation Discovery, Search, and Filtering tests for SmartSurplus.

Covers:
- Listing open donations with status, availability, and deadline urgency indicators
- Excluding expired, fully claimed, and cancelled donations
- Search only (food name, location, description)
- Category filter only
- Combined search + category + location + min_qty + urgency + sort
- Empty results messaging (with and without active filters)
- Clear/reset filters control and preserved input values
- Malformed / SQL-injection / wildcard / invalid numeric filter inputs

Run with:
    .venv/bin/python -m unittest discover -s tests -v
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

os.environ.pop("AZURE_SQL_CONNECTION_STRING", None)

import app as app_mod
import db


class SmartSurplusDiscoveryTests(unittest.TestCase):
    """Test suite for /donations discovery, search, filters, and edge cases."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_instance = app_mod.app.instance_path
        app_mod.app.instance_path = self.tmp_dir.name
        app_mod.app.config["TESTING"] = True

        with app_mod.app.app_context():
            db.init_db()
            self._seed_listings()

        self.client = app_mod.app.test_client()

    def tearDown(self):
        app_mod.app.instance_path = self.orig_instance
        app_mod.app.config["TESTING"] = False
        self.tmp_dir.cleanup()

    def _seed_listings(self):
        now = datetime.now()
        iso = lambda hours: (now + timedelta(hours=hours)).isoformat(timespec="seconds")

        donor_id = db.insert(
            "INSERT INTO users (full_name, email, password_hash, role, created_at) "
            "VALUES (?, ?, ?, 'donor', ?)",
            ("Green Kitchen", "gk@example.com", generate_password_hash("Strong@123"), iso(0)),
        )
        cats = {r["name"]: r["id"] for r in db.query("SELECT id, name FROM categories")}

        # 1. Active, urgent (expires in 3h), plenty of stock
        db.insert(
            "INSERT INTO donations (donor_id, category_id, food_name, description, "
            "quantity, remaining_quantity, location, deadline, status, created_at) "
            "VALUES (?, ?, 'Veg Biryani', 'Hot spiced rice', 40, 25, 'Gomti Nagar', ?, 'available', ?)",
            (donor_id, cats["Cooked meals"], iso(3), iso(-1)),
        )
        # 2. Active, collect today (12h), low stock (4 of 30)
        db.insert(
            "INSERT INTO donations (donor_id, category_id, food_name, description, "
            "quantity, remaining_quantity, location, deadline, status, created_at) "
            "VALUES (?, ?, 'Whole Wheat Bread', 'Fresh bakery loaves', 30, 4, 'Hazratganj', ?, 'available', ?)",
            (donor_id, cats["Bakery"], iso(12), iso(-2)),
        )
        # 3. Active, normal (48h)
        db.insert(
            "INSERT INTO donations (donor_id, category_id, food_name, description, "
            "quantity, remaining_quantity, location, deadline, status, created_at) "
            "VALUES (?, ?, 'Fresh Apples', 'Crisp orchard fruit', 50, 50, 'Gomti Nagar', ?, 'available', ?)",
            (donor_id, cats["Fruits and vegetables"], iso(48), iso(-3)),
        )
        # 4. Expired donation (deadline in past) -> MUST BE EXCLUDED
        db.insert(
            "INSERT INTO donations (donor_id, category_id, food_name, description, "
            "quantity, remaining_quantity, location, deadline, status, created_at) "
            "VALUES (?, ?, 'Expired Soup', 'Past deadline', 15, 15, 'Aliganj', ?, 'available', ?)",
            (donor_id, cats["Cooked meals"], iso(-2), iso(-6)),
        )
        # 5. Fully claimed donation -> MUST BE EXCLUDED
        db.insert(
            "INSERT INTO donations (donor_id, category_id, food_name, description, "
            "quantity, remaining_quantity, location, deadline, status, created_at) "
            "VALUES (?, ?, 'Claimed Pasta', 'All reserved', 20, 0, 'Aliganj', ?, 'claimed', ?)",
            (donor_id, cats["Cooked meals"], iso(8), iso(-1)),
        )
        # 6. Cancelled donation -> MUST BE EXCLUDED
        db.insert(
            "INSERT INTO donations (donor_id, category_id, food_name, description, "
            "quantity, remaining_quantity, location, deadline, status, created_at) "
            "VALUES (?, ?, 'Cancelled Milk', 'No longer offered', 10, 10, 'Indira Nagar', ?, 'cancelled', ?)",
            (donor_id, cats["Dairy"], iso(8), iso(-1)),
        )
        db.commit()

    def test_open_listings_display_fields_and_exclude_inactive(self):
        resp = self.client.get("/donations")
        self.assertEqual(resp.status_code, 200)

        # Open donations appear with fields, status, availability & urgency badges
        self.assertIn(b"3 listing(s) available", resp.data)
        self.assertIn(b"Veg Biryani", resp.data)
        self.assertIn(b"Whole Wheat Bread", resp.data)
        self.assertIn(b"Fresh Apples", resp.data)
        self.assertIn(b"25 of 40 portions", resp.data)
        self.assertIn(b"status-available", resp.data)
        self.assertIn(b"Urgent", resp.data)
        self.assertIn(b"Collect today", resp.data)
        self.assertIn(b"Low stock", resp.data)
        self.assertIn(b"Clear filters", resp.data)

        # Expired, claimed, and cancelled donations are excluded
        self.assertNotIn(b"Expired Soup", resp.data)
        self.assertNotIn(b"Claimed Pasta", resp.data)
        self.assertNotIn(b"Cancelled Milk", resp.data)

    def test_search_only_by_food_location_and_description(self):
        # By food name
        by_food = self.client.get("/donations?q=Biryani")
        self.assertIn(b"Veg Biryani", by_food.data)
        self.assertNotIn(b"Whole Wheat Bread", by_food.data)
        self.assertIn(b'value="Biryani"', by_food.data)

        # By location
        by_loc = self.client.get("/donations?q=Hazratganj")
        self.assertIn(b"Whole Wheat Bread", by_loc.data)
        self.assertNotIn(b"Veg Biryani", by_loc.data)

        # By description
        by_desc = self.client.get("/donations?q=orchard")
        self.assertIn(b"Fresh Apples", by_desc.data)
        self.assertNotIn(b"Veg Biryani", by_desc.data)

    def test_category_only_filter(self):
        resp = self.client.get("/donations?category=Bakery")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"1 listing(s) available", resp.data)
        self.assertIn(b"Whole Wheat Bread", resp.data)
        self.assertNotIn(b"Veg Biryani", resp.data)
        self.assertNotIn(b"Fresh Apples", resp.data)
        self.assertIn(b'<option value="Bakery" selected>', resp.data)

    def test_search_and_filters_combined_and_preserved(self):
        # Location 'Gomti Nagar' matches both Veg Biryani and Fresh Apples;
        # combining with category='Cooked meals' narrows to Veg Biryani only.
        resp = self.client.get(
            "/donations?q=spiced&category=Cooked+meals&location=Gomti+Nagar&min_qty=10&urgency=6h&sort=portions"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"1 listing(s) available", resp.data)
        self.assertIn(b"Veg Biryani", resp.data)
        self.assertNotIn(b"Fresh Apples", resp.data)
        self.assertNotIn(b"Whole Wheat Bread", resp.data)

        # Preserved input values
        self.assertIn(b'value="spiced"', resp.data)
        self.assertIn(b'value="Gomti Nagar"', resp.data)
        self.assertIn(b'value="10"', resp.data)
        self.assertIn(b'<option value="6h" selected>', resp.data)
        self.assertIn(b'<option value="portions" selected>', resp.data)

        # Sort by portions puts Fresh Apples (50) first
        sorted_resp = self.client.get("/donations?sort=portions")
        html = sorted_resp.get_data(as_text=True)
        self.assertLess(html.index("Fresh Apples"), html.index("Veg Biryani"))
        self.assertLess(html.index("Veg Biryani"), html.index("Whole Wheat Bread"))

    def test_empty_results_with_and_without_filters(self):
        # Active filters returning no results show reset guidance
        no_match = self.client.get("/donations?q=Pizza&category=Bakery")
        self.assertEqual(no_match.status_code, 200)
        self.assertIn(b"No donations found", no_match.data)
        self.assertIn(b"Reset search &amp; filters", no_match.data)

        # When database has zero open donations, shows general empty message
        with app_mod.app.app_context():
            db.execute("UPDATE donations SET status = 'cancelled'")
            db.commit()
        empty_db = self.client.get("/donations")
        self.assertEqual(empty_db.status_code, 200)
        self.assertIn(b"No donations found", empty_db.data)
        self.assertIn(b"No surplus food donations are currently available.", empty_db.data)

    def test_malformed_and_adversarial_inputs_do_not_crash(self):
        malformed_queries = [
            "/donations?q=' OR 1=1 --",
            "/donations?q=%25%5F\\",
            "/donations?q=hello%00world&location=test%00",
            "/donations?min_qty=abc",
            "/donations?min_qty=-10",
            "/donations?min_qty=999999999999999999999999",
            "/donations?urgency=invalid&sort=DROP+TABLE+donations",
            f"/donations?q={'X' * 500}&category={'Y' * 200}",
        ]
        for url in malformed_queries:
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200)
                self.assertIn(b"Available Food Donations", resp.data)

        # Check invalid min_qty shows a helpful message
        bad_qty = self.client.get("/donations?min_qty=abc")
        self.assertIn(b"Minimum portions filter must be a valid whole number.", bad_qty.data)


if __name__ == "__main__":
    unittest.main()

