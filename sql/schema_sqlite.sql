-- SmartSurplus schema for LOCAL development (SQLite).
-- Same logical design as sql/schema_sqlserver.sql (Azure SQL Database).

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name       TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('donor', 'recipient', 'admin')),
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS donations (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    donor_id           INTEGER NOT NULL REFERENCES users(id),
    category_id        INTEGER NOT NULL REFERENCES categories(id),
    food_name          TEXT NOT NULL,
    quantity           INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 100000),
    remaining_quantity INTEGER NOT NULL CHECK (remaining_quantity >= 0),
    location           TEXT NOT NULL,
    deadline           TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'available'
                       CHECK (status IN ('available', 'claimed', 'cancelled')),
    created_at         TEXT NOT NULL,
    CHECK (remaining_quantity <= quantity)
);

CREATE TABLE IF NOT EXISTS requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    donation_id  INTEGER NOT NULL REFERENCES donations(id),
    recipient_id INTEGER NOT NULL REFERENCES users(id),
    quantity     INTEGER NOT NULL CHECK (quantity >= 1),
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'collected', 'cancelled')),
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NULL REFERENCES users(id),
    action     TEXT NOT NULL,
    detail     TEXT NULL,
    ip_address TEXT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_donations_status_deadline ON donations (status, deadline);
CREATE INDEX IF NOT EXISTS ix_donations_donor ON donations (donor_id);
CREATE INDEX IF NOT EXISTS ix_requests_recipient ON requests (recipient_id);
CREATE INDEX IF NOT EXISTS ix_requests_donation ON requests (donation_id);

CREATE VIEW IF NOT EXISTS v_open_donations AS
SELECT d.id, d.food_name, d.quantity, d.remaining_quantity, d.location,
       d.deadline, d.created_at, c.name AS category, u.full_name AS donor_name
FROM donations d
JOIN categories c ON c.id = d.category_id
JOIN users u      ON u.id = d.donor_id
WHERE d.status = 'available' AND d.remaining_quantity > 0;

INSERT OR IGNORE INTO categories (name) VALUES
 ('Cooked meals'), ('Bakery'), ('Fruits and vegetables'),
 ('Packaged food'), ('Dairy'), ('Other');
