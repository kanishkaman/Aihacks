"""Seeds the demo SQLite database with sample e-commerce data."""
from __future__ import annotations

import os
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "demo.db"

COUNTRIES = ["US", "IN", "GB", "DE", "FR", "JP", "BR", "CA", "AU", "SG"]
CATEGORIES = ["electronics", "books", "clothing", "home", "toys", "beauty", "sports", "grocery"]
STATUSES = ["pending", "paid", "shipped", "delivered", "cancelled", "refunded"]

NUM_USERS = 10_000
NUM_PRODUCTS = 1_000
NUM_ORDERS = 100_000
ITEMS_PER_ORDER = 3


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS order_items;
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS users;

        CREATE TABLE users (
            id          INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            email       TEXT NOT NULL,
            country     TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE products (
            id          INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            category    TEXT NOT NULL,
            price       REAL NOT NULL
        );

        CREATE TABLE orders (
            id          INTEGER PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            status      TEXT NOT NULL,
            total       REAL NOT NULL,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE order_items (
            id          INTEGER PRIMARY KEY,
            order_id    INTEGER NOT NULL,
            product_id  INTEGER NOT NULL,
            quantity    INTEGER NOT NULL,
            unit_price  REAL NOT NULL
        );
        """
    )
    conn.commit()


def seed(conn: sqlite3.Connection, seed: int = 42) -> None:
    rng = random.Random(seed)
    cur = conn.cursor()

    users = [
        (
            i,
            f"User{i}",
            f"user{i}@example.com",
            rng.choice(COUNTRIES),
            (datetime(2023, 1, 1) + timedelta(days=rng.randint(0, 800))).isoformat(timespec="seconds"),
        )
        for i in range(1, NUM_USERS + 1)
    ]
    cur.executemany("INSERT INTO users VALUES (?,?,?,?,?)", users)

    products = [
        (i, f"Product {i}", rng.choice(CATEGORIES), round(rng.uniform(5, 500), 2))
        for i in range(1, NUM_PRODUCTS + 1)
    ]
    cur.executemany("INSERT INTO products VALUES (?,?,?,?)", products)

    orders = []
    items = []
    item_id = 1
    for oid in range(1, NUM_ORDERS + 1):
        uid = rng.randint(1, NUM_USERS)
        status = rng.choices(STATUSES, weights=[10, 20, 25, 30, 10, 5])[0]
        created = datetime(2024, 1, 1) + timedelta(minutes=rng.randint(0, 60 * 24 * 500))
        n_items = max(1, int(rng.gauss(ITEMS_PER_ORDER, 1.5)))
        total = 0.0
        order_items_tmp = []
        for _ in range(n_items):
            pid = rng.randint(1, NUM_PRODUCTS)
            qty = rng.randint(1, 4)
            price = round(rng.uniform(5, 500), 2)
            total += qty * price
            order_items_tmp.append((item_id, oid, pid, qty, price))
            item_id += 1
        orders.append((oid, uid, status, round(total, 2), created.isoformat(timespec="seconds")))
        items.extend(order_items_tmp)

    cur.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", orders)
    cur.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", items)

    conn.commit()
    cur.execute("ANALYZE")
    conn.commit()


def reset_database(db_path: Path = DB_PATH) -> Path:
    if db_path.exists():
        db_path.unlink()
    conn = _connect(db_path)
    try:
        create_schema(conn)
        seed(conn)
    finally:
        conn.close()
    return db_path


def ensure_database(db_path: Path = DB_PATH) -> Path:
    if not db_path.exists():
        reset_database(db_path)
    return db_path


if __name__ == "__main__":
    path = reset_database()
    size_mb = os.path.getsize(path) / 1e6
    print(f"Seeded {path} ({size_mb:.1f} MB)")
