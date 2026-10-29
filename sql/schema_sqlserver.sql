-- SmartSurplus schema for AZURE SQL DATABASE (T-SQL).
-- Run in the portal's Query editor, or:  flask --app app init-azure-db
-- "GO" lines separate batches (CREATE VIEW must be first in its batch).
-- Safe to run more than once.

IF OBJECT_ID('dbo.users', 'U') IS NULL
CREATE TABLE dbo.users (
    id              INT IDENTITY(1,1) CONSTRAINT pk_users PRIMARY KEY,
    full_name       NVARCHAR(100) NOT NULL,
    email           NVARCHAR(255) NOT NULL CONSTRAINT uq_users_email UNIQUE,
    password_hash   NVARCHAR(255) NOT NULL,
    role            VARCHAR(20)   NOT NULL
                    CONSTRAINT ck_users_role CHECK (role IN ('donor', 'recipient', 'admin')),
    failed_attempts INT           NOT NULL CONSTRAINT df_users_failed DEFAULT 0,
    locked_until    DATETIME2(0)  NULL,
    created_at      DATETIME2(0)  NOT NULL
);
GO

IF OBJECT_ID('dbo.categories', 'U') IS NULL
CREATE TABLE dbo.categories (
    id   INT IDENTITY(1,1) CONSTRAINT pk_categories PRIMARY KEY,
    name NVARCHAR(60) NOT NULL CONSTRAINT uq_categories_name UNIQUE
);
GO

IF OBJECT_ID('dbo.donations', 'U') IS NULL
CREATE TABLE dbo.donations (
    id                 INT IDENTITY(1,1) CONSTRAINT pk_donations PRIMARY KEY,
    donor_id           INT NOT NULL CONSTRAINT fk_donations_donor    REFERENCES dbo.users(id),
    category_id        INT NOT NULL CONSTRAINT fk_donations_category REFERENCES dbo.categories(id),
    food_name          NVARCHAR(100) NOT NULL,
    quantity           INT NOT NULL CONSTRAINT ck_donations_qty CHECK (quantity BETWEEN 1 AND 100000),
    remaining_quantity INT NOT NULL CONSTRAINT ck_donations_rem CHECK (remaining_quantity >= 0),
    location           NVARCHAR(200) NOT NULL,
    deadline           DATETIME2(0) NOT NULL,
    status             VARCHAR(20) NOT NULL CONSTRAINT df_donations_status DEFAULT 'available'
                       CONSTRAINT ck_donations_status CHECK (status IN ('available', 'claimed', 'cancelled')),
    created_at         DATETIME2(0) NOT NULL,
    CONSTRAINT ck_donations_rem_le_qty CHECK (remaining_quantity <= quantity)
);
GO

IF OBJECT_ID('dbo.requests', 'U') IS NULL
CREATE TABLE dbo.requests (
    id           INT IDENTITY(1,1) CONSTRAINT pk_requests PRIMARY KEY,
    donation_id  INT NOT NULL CONSTRAINT fk_requests_donation  REFERENCES dbo.donations(id),
    recipient_id INT NOT NULL CONSTRAINT fk_requests_recipient REFERENCES dbo.users(id),
    quantity     INT NOT NULL CONSTRAINT ck_requests_qty CHECK (quantity >= 1),
    status       VARCHAR(20) NOT NULL CONSTRAINT df_requests_status DEFAULT 'pending'
                 CONSTRAINT ck_requests_status CHECK (status IN ('pending', 'collected', 'cancelled')),
    created_at   DATETIME2(0) NOT NULL
);
GO

IF OBJECT_ID('dbo.audit_log', 'U') IS NULL
CREATE TABLE dbo.audit_log (
    id         INT IDENTITY(1,1) CONSTRAINT pk_audit_log PRIMARY KEY,
    user_id    INT NULL CONSTRAINT fk_audit_user REFERENCES dbo.users(id),
    action     VARCHAR(50)    NOT NULL,
    detail     NVARCHAR(500)  NULL,
    ip_address VARCHAR(45)    NULL,
    created_at DATETIME2(0)   NOT NULL
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_donations_status_deadline')
    CREATE INDEX ix_donations_status_deadline ON dbo.donations (status, deadline);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_donations_donor')
    CREATE INDEX ix_donations_donor ON dbo.donations (donor_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_requests_recipient')
    CREATE INDEX ix_requests_recipient ON dbo.requests (recipient_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_requests_donation')
    CREATE INDEX ix_requests_donation ON dbo.requests (donation_id);
GO

CREATE OR ALTER VIEW dbo.v_open_donations AS
SELECT d.id, d.food_name, d.quantity, d.remaining_quantity, d.location,
       d.deadline, d.created_at, c.name AS category, u.full_name AS donor_name
FROM dbo.donations d
JOIN dbo.categories c ON c.id = d.category_id
JOIN dbo.users u      ON u.id = d.donor_id
WHERE d.status = 'available' AND d.remaining_quantity > 0;
GO

MERGE dbo.categories AS t
USING (VALUES (N'Cooked meals'), (N'Bakery'), (N'Fruits and vegetables'),
              (N'Packaged food'), (N'Dairy'), (N'Other')) AS s(name)
ON t.name = s.name
WHEN NOT MATCHED THEN INSERT (name) VALUES (s.name);
GO

/* ---------- Least-privilege app login (run as the server admin) ----------
   Do this in the SmartSurplus database, then use this user in the app's
   connection string instead of the admin login.

   CREATE USER smartsurplus_app WITH PASSWORD = '<strong-password>';
   GRANT SELECT, INSERT, UPDATE ON dbo.users      TO smartsurplus_app;
   GRANT SELECT                 ON dbo.categories TO smartsurplus_app;
   GRANT SELECT, INSERT, UPDATE ON dbo.donations  TO smartsurplus_app;
   GRANT SELECT, INSERT, UPDATE ON dbo.requests   TO smartsurplus_app;
   GRANT SELECT, INSERT         ON dbo.audit_log  TO smartsurplus_app;
   GRANT SELECT                 ON dbo.v_open_donations TO smartsurplus_app;
   -- No DELETE, no ALTER, no DROP: a SQL-injection bug cannot wipe tables.
*/
