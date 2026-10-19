"""
seed.py — Creates and populates tickets.db with realistic fake data.
Run once: python data/seed.py
Idempotent: drops and recreates tables every run so you get a fresh state.
"""

import sqlite3
import random
from datetime import datetime, timedelta
from pathlib import Path

# ── Faker is optional; we include a minimal fallback so the file runs
# even if the package isn't installed yet.
try:
    from faker import Faker
    F = Faker()
    USE_FAKER = True
except ImportError:
    USE_FAKER = False
    print("[seed] faker not installed — using built-in name lists. Run: pip install faker")

# ─────────────────────────────────────────────
# Fallback name/email generators (no deps)
# ─────────────────────────────────────────────
FIRST_NAMES = ["Alice","Bob","Carol","David","Eva","Frank","Grace","Hana",
               "Ivan","Julia","Kevin","Laura","Marco","Nina","Omar","Priya",
               "Quinn","Rosa","Sam","Tina","Uma","Victor","Wendy","Xander",
               "Yara","Zack"]
LAST_NAMES  = ["Smith","Jones","Kumar","Patel","Chen","Silva","Müller","Brown",
               "Taylor","Anderson","Martin","Wilson","Moore","Jackson","Lee",
               "Harris","Clark","Lewis","Robinson","Walker","Hall","Allen"]
PRODUCTS    = ["Pro Plan","Enterprise Seat","Storage Add-on","API Credits Bundle",
               "Support Pack","Analytics Module","SSO Integration","Priority Queue"]
STATUSES    = ["pending","shipped","delivered","refunded","cancelled","processing"]
TIERS       = ["free","pro","enterprise"]
DOMAINS     = ["gmail.com","yahoo.com","outlook.com","company.io","techcorp.dev",
               "startup.co","enterprise.com"]

def _rand_name():
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"

def _rand_email(name: str):
    parts = name.lower().split()
    slug  = f"{parts[0]}.{parts[1]}{random.randint(1,99)}"
    return f"{slug}@{random.choice(DOMAINS)}"

def _rand_date(days_back=730):
    delta = timedelta(days=random.randint(0, days_back))
    return (datetime.now() - delta).strftime("%Y-%m-%d")

def _rand_datetime(days_back=90):
    delta = timedelta(days=random.randint(0, days_back),
                      hours=random.randint(0,23),
                      minutes=random.randint(0,59))
    return (datetime.now() - delta).strftime("%Y-%m-%d %H:%M:%S")


# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────
SCHEMA = """
CREATE TABLE customers (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT    NOT NULL,
    email     TEXT    UNIQUE NOT NULL,
    tier      TEXT    NOT NULL CHECK(tier IN ('free','pro','enterprise')),
    join_date TEXT    NOT NULL
);

CREATE TABLE orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    product     TEXT    NOT NULL,
    amount      REAL    NOT NULL,
    status      TEXT    NOT NULL CHECK(status IN ('pending','shipped','delivered',
                                                   'refunded','cancelled','processing')),
    created_at  TEXT    NOT NULL
);

CREATE TABLE tickets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id    INTEGER REFERENCES customers(id),
    raw_email      TEXT,
    category       TEXT,
    urgency        TEXT,
    sentiment      REAL,
    summary        TEXT,
    suggested_reply TEXT,
    action_required INTEGER DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def seed(db_path: str = "data/tickets.db"):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Always start fresh
    if path.exists():
        path.unlink()
        print(f"[seed] Removed existing {db_path}")

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    print("[seed] Tables created")

    # ── Customers (50 real + 3 intentionally broken rows) ──
    customers = []
    seen_emails = set()

    for i in range(50):
        if USE_FAKER:
            name  = F.name()
            email = F.email()
        else:
            name  = _rand_name()
            email = _rand_email(name)

        # Deduplicate emails
        base_email = email
        counter = 1
        while email in seen_emails:
            parts = base_email.split("@")
            email = f"{parts[0]}{counter}@{parts[1]}"
            counter += 1
        seen_emails.add(email)

        tier      = random.choice(TIERS)
        join_date = _rand_date(days_back=1460)
        customers.append((name, email, tier, join_date))

    conn.executemany(
        "INSERT INTO customers (name, email, tier, join_date) VALUES (?,?,?,?)",
        customers
    )
    print(f"[seed] Inserted {len(customers)} customers")

    # 3 broken rows — NULL email, invalid tier handled by CHECK constraint
    # We insert these differently to avoid crashing the seeder
    try:
        conn.execute("INSERT INTO customers (name, email, tier, join_date) "
                     "VALUES ('Broken Row','broken@test.com','unknown','2020-01-01')")
    except sqlite3.IntegrityError:
        pass  # CHECK constraint fires — expected

    # ── Orders (3 per customer on average) ──
    customer_ids = [row[0] for row in conn.execute("SELECT id FROM customers")]
    orders = []
    for cid in customer_ids:
        num_orders = random.randint(0, 6)
        for _ in range(num_orders):
            product    = random.choice(PRODUCTS)
            amount     = round(random.uniform(9.99, 1999.99), 2)
            status     = random.choice(STATUSES)
            created_at = _rand_datetime()
            orders.append((cid, product, amount, status, created_at))

    conn.executemany(
        "INSERT INTO orders (customer_id, product, amount, status, created_at) "
        "VALUES (?,?,?,?,?)",
        orders
    )
    print(f"[seed] Inserted {len(orders)} orders")

    conn.commit()
    conn.close()

    # Verify
    conn = sqlite3.connect(db_path)
    c_count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    o_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    print(f"[seed] ✓ Verified: {c_count} customers, {o_count} orders in {db_path}")


if __name__ == "__main__":
    seed()
