"""Fill the local SQLite database with demo data for the DBMS practical.
Usage:  python seed_demo.py          (safe to re-run; skips if demo users exist)
Demo logins (password for all: Demo@1234):
    donor1@demo.com  donor2@demo.com  (donors)
    ngo1@demo.com    ngo2@demo.com    (recipients)
    admin@demo.com                    (admin)
"""
import os
os.environ.pop("AZURE_SQL_CONNECTION_STRING", None)   # always seed the local DB
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

import db
from app import app

PW = generate_password_hash("Demo@1234")
now = datetime.now()
iso = lambda d: d.isoformat(timespec="seconds")

with app.app_context():
    if db.query_one("SELECT id FROM users WHERE email = ?", ("donor1@demo.com",)):
        print("Demo data already present."); raise SystemExit

    users = [("Green Leaf Cafe", "donor1@demo.com", "donor"),
             ("Sweet Crumbs Bakery", "donor2@demo.com", "donor"),
             ("Hope Foundation", "ngo1@demo.com", "recipient"),
             ("City Food Bank", "ngo2@demo.com", "recipient"),
             ("Admin", "admin@demo.com", "admin")]
    ids = {}
    for name, email, role in users:
        ids[email] = db.insert(
            "INSERT INTO users (full_name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
            (name, email, PW, role, iso(now)))

    cat = {r["name"]: r["id"] for r in db.query("SELECT id, name FROM categories")}
    donations = [("donor1@demo.com", "Veg biryani trays", "Cooked meals", 40, "Gomti Nagar, Lucknow", 6),
                 ("donor1@demo.com", "Fresh salad boxes", "Fruits and vegetables", 25, "Hazratganj, Lucknow", 5),
                 ("donor2@demo.com", "Bread loaves", "Bakery", 60, "Aliganj, Lucknow", 10),
                 ("donor2@demo.com", "Milk packets", "Dairy", 30, "Indira Nagar, Lucknow", 8)]
    did = []
    for donor, food, c, qty, loc, hrs in donations:
        did.append(db.insert(
            "INSERT INTO donations (donor_id, category_id, food_name, quantity, remaining_quantity, "
            "location, deadline, status, created_at) VALUES (?,?,?,?,?,?,?, 'available', ?)",
            (ids[donor], cat[c], food, qty, qty, loc, iso(now + timedelta(hours=hrs)), iso(now))))

    for dn, rec, qty in [(0, "ngo1@demo.com", 15), (0, "ngo2@demo.com", 10), (2, "ngo1@demo.com", 20)]:
        db.execute("UPDATE donations SET remaining_quantity = remaining_quantity - ? WHERE id = ?", (qty, did[dn]))
        db.insert("INSERT INTO requests (donation_id, recipient_id, quantity, status, created_at) "
                  "VALUES (?,?,?, 'pending', ?)", (did[dn], ids[rec], qty, iso(now)))
    db.commit()
    print("Demo data inserted. Login: donor1@demo.com / Demo@1234")
