-- SmartSurplus : DBMS practical demo queries (run in DB Browser for SQLite or `sqlite3 instance/smartsurplus_v2.db`)
-- Order follows a typical practical/viva: DDL -> DML -> constraints -> joins -> aggregates -> subquery -> view -> index -> transaction.

-- ===== 1. Show the schema (DDL) =====
SELECT name, type FROM sqlite_master WHERE type IN ('table','view','index') ORDER BY type, name;
PRAGMA table_info(donations);
PRAGMA foreign_key_list(donations);

-- ===== 2. Simple SELECT / WHERE / ORDER BY =====
SELECT * FROM users;
SELECT food_name, quantity, location FROM donations WHERE quantity > 30 ORDER BY quantity DESC;
SELECT * FROM donations WHERE food_name LIKE '%bread%';

-- ===== 3. INSERT / UPDATE / DELETE (DML) =====
INSERT INTO categories (name) VALUES ('Beverages');
UPDATE categories SET name = 'Drinks' WHERE name = 'Beverages';
DELETE FROM categories WHERE name = 'Drinks';

-- ===== 4. Constraints in action (each of these SHOULD FAIL - show the error) =====
-- CHECK: quantity must be 1..100000
INSERT INTO donations (donor_id, category_id, food_name, quantity, remaining_quantity, location, deadline, created_at)
VALUES (1, 1, 'Bad', 0, 0, 'X', '2030-01-01T10:00:00', '2026-01-01T10:00:00');
-- UNIQUE: duplicate email
INSERT INTO users (full_name, email, password_hash, role, created_at)
VALUES ('Dup', 'donor1@demo.com', 'x', 'donor', '2026-01-01T10:00:00');
-- FOREIGN KEY: donor 999 does not exist
INSERT INTO donations (donor_id, category_id, food_name, quantity, remaining_quantity, location, deadline, created_at)
VALUES (999, 1, 'Ghost', 5, 5, 'X', '2030-01-01T10:00:00', '2026-01-01T10:00:00');
-- CHECK on role
INSERT INTO users (full_name, email, password_hash, role, created_at)
VALUES ('Hacker', 'h@x.com', 'x', 'superuser', '2026-01-01T10:00:00');

-- ===== 5. JOINS =====
-- INNER JOIN (3 tables)
SELECT d.food_name, c.name AS category, u.full_name AS donor
FROM donations d
JOIN categories c ON c.id = d.category_id
JOIN users u      ON u.id = d.donor_id;
-- LEFT JOIN: every donation, even with no requests
SELECT d.food_name, COUNT(r.id) AS requests
FROM donations d LEFT JOIN requests r ON r.donation_id = d.id
GROUP BY d.id, d.food_name;
-- Who requested what
SELECT u.full_name AS recipient, d.food_name, r.quantity, r.status
FROM requests r
JOIN users u     ON u.id = r.recipient_id
JOIN donations d ON d.id = r.donation_id;

-- ===== 6. Aggregates / GROUP BY / HAVING =====
SELECT u.full_name, COUNT(*) AS listings, SUM(d.quantity) AS total_portions
FROM donations d JOIN users u ON u.id = d.donor_id
GROUP BY u.full_name;
SELECT c.name, SUM(d.quantity) AS portions
FROM donations d JOIN categories c ON c.id = d.category_id
GROUP BY c.name HAVING SUM(d.quantity) > 30;
SELECT MIN(quantity), MAX(quantity), AVG(quantity) FROM donations;

-- ===== 7. Subqueries =====
-- Donations bigger than the average
SELECT food_name, quantity FROM donations WHERE quantity > (SELECT AVG(quantity) FROM donations);
-- Donors who have at least one request on their food
SELECT full_name FROM users
WHERE id IN (SELECT d.donor_id FROM donations d JOIN requests r ON r.donation_id = d.id);
-- Donations nobody has requested (NOT EXISTS)
SELECT food_name FROM donations d WHERE NOT EXISTS (SELECT 1 FROM requests r WHERE r.donation_id = d.id);

-- ===== 8. VIEW =====
SELECT * FROM v_open_donations;

-- ===== 9. INDEX (show the query plan uses it) =====
EXPLAIN QUERY PLAN SELECT * FROM donations WHERE status = 'available' AND deadline > '2026-01-01';

-- ===== 10. TRANSACTION: commit vs rollback (ACID) =====
BEGIN;
UPDATE donations SET remaining_quantity = remaining_quantity - 5 WHERE id = 1;
SELECT id, remaining_quantity FROM donations WHERE id = 1;   -- changed inside the transaction
ROLLBACK;
SELECT id, remaining_quantity FROM donations WHERE id = 1;   -- back to the old value (Atomicity)

-- ===== 11. Integrity: stock can never go negative (CHECK) - this SHOULD FAIL =====
UPDATE donations SET remaining_quantity = remaining_quantity - 1000 WHERE id = 1;

-- ===== 12. Audit trail =====
SELECT action, COUNT(*) FROM audit_log GROUP BY action;
