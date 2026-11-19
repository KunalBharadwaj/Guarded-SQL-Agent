"""Deterministic seed data for the business schema.

Uses a fixed RNG seed so the database — and therefore the gold query results and
every eval number — is fully reproducible. Called by build_db.py.
"""
from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta

SEED = 42
START = date(2024, 1, 1)
DAYS = 640  # ~Jan 2024 .. early Oct 2025


def _d(offset: int) -> str:
    return (START + timedelta(days=offset)).isoformat()


def seed(conn: sqlite3.Connection) -> None:
    rng = random.Random(SEED)
    cur = conn.cursor()

    # --- categories (with subcategories via parent_id) ---------------------
    categories = [
        (1, "Electronics", None), (2, "Computers", 1), (3, "Phones", 1),
        (4, "Home", None), (5, "Kitchen", 4), (6, "Furniture", 4),
        (7, "Books", None), (8, "Apparel", None), (9, "Sports", None),
        (10, "Toys", None),
    ]
    cur.executemany("INSERT INTO categories VALUES (?,?,?)", categories)

    # --- suppliers ---------------------------------------------------------
    countries = ["USA", "Germany", "China", "Japan", "UK", "India", "Canada"]
    suppliers = [
        (i, f"Supplier {i}", rng.choice(countries),
         rng.choice([3, 5, 7, 10, 14, None]))
        for i in range(1, 16)
    ]
    cur.executemany("INSERT INTO suppliers VALUES (?,?,?,?)", suppliers)

    # --- products ----------------------------------------------------------
    products = []
    for i in range(1, 81):
        cat = rng.randint(1, 10)
        sup = rng.randint(1, 15)
        price = round(rng.uniform(5, 800), 2)
        active = 1 if rng.random() > 0.15 else 0  # ~15% discontinued
        products.append((i, f"Product {i}", cat, sup, price, active))
    cur.executemany("INSERT INTO products VALUES (?,?,?,?,?,?)", products)

    # --- inventory (one row per product) -----------------------------------
    warehouses = ["EAST", "WEST", "CENTRAL"]
    inventory = [
        (p[0], rng.randint(0, 500), rng.choice([5, 10, 20]), rng.choice(warehouses))
        for p in products
    ]
    cur.executemany("INSERT INTO inventory VALUES (?,?,?,?)", inventory)

    # --- customers ---------------------------------------------------------
    firsts = ["Alice", "Bob", "Carol", "David", "Eve", "Frank", "Grace", "Heidi",
              "Ivan", "Judy", "Mallory", "Niaj", "Olivia", "Peggy", "Rupert",
              "Sybil", "Trent", "Victor", "Walter", "Yvonne"]
    lasts = ["Smith", "Jones", "Brown", "Garcia", "Miller", "Davis", "Lopez",
             "Wilson", "Khan", "Patel", "Chen", "Kim", "Mueller", "Rossi"]
    cust_countries = ["USA", "USA", "USA", "UK", "Germany", "Canada", "India", "Japan"]
    customers = []
    for i in range(1, 121):
        customers.append((
            i, rng.choice(firsts), rng.choice(lasts),
            f"customer{i}@example.com", rng.choice(cust_countries),
            _d(rng.randint(0, DAYS - 30)),
            1 if rng.random() > 0.1 else 0,
        ))
    cur.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?)", customers)

    # --- employees (with manager org chart) --------------------------------
    employees = [
        (1, "Diana", "Prince", "VP Sales", _d(0), None),
        (2, "Clark", "Kent", "Sales Manager", _d(5), 1),
        (3, "Bruce", "Wayne", "Sales Manager", _d(5), 1),
    ]
    for i in range(4, 16):
        employees.append((i, rng.choice(firsts), rng.choice(lasts),
                          "Sales Rep", _d(rng.randint(10, 200)),
                          rng.choice([2, 3])))
    cur.executemany("INSERT INTO employees VALUES (?,?,?,?,?,?)", employees)

    # --- orders + order_items + payments -----------------------------------
    statuses = ["pending", "shipped", "delivered", "delivered", "delivered",
                "cancelled", "returned"]
    methods = ["card", "card", "paypal", "wire", "gift_card"]
    order_id = 0
    item_id = 0
    payment_id = 0
    orders, items, payments = [], [], []
    for _ in range(600):
        order_id += 1
        # customers 111..120 deliberately never order (realistic: dormant signups)
        cust = rng.randint(1, 110)
        emp = rng.choice([None] + list(range(4, 16)))
        odate = _d(rng.randint(0, DAYS))
        status = rng.choice(statuses)
        ship = rng.choice(cust_countries)
        orders.append((order_id, cust, emp, odate, status, ship))

        # 1–5 line items
        order_total = 0.0
        for _ in range(rng.randint(1, 5)):
            item_id += 1
            prod = rng.randint(1, 80)
            qty = rng.randint(1, 8)
            price = round(rng.uniform(5, 800), 2)
            disc = rng.choice([0.0, 0.0, 0.0, 0.05, 0.1, 0.2])
            items.append((item_id, order_id, prod, qty, price, disc))
            order_total += qty * price * (1 - disc)

        # payment: most captured, some unpaid (NULL paid_date), some refunded
        if status != "cancelled":
            payment_id += 1
            if status == "returned":
                pstatus = "refunded"
            elif rng.random() < 0.08:
                pstatus = "failed"
            else:
                pstatus = "captured"
            paid = None if pstatus == "failed" else odate
            payments.append((payment_id, order_id, round(order_total, 2),
                             rng.choice(methods), paid, pstatus))

    cur.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?)", orders)
    cur.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?)", items)
    cur.executemany("INSERT INTO payments VALUES (?,?,?,?,?,?)", payments)

    # --- reviews -----------------------------------------------------------
    reviews = []
    rid = 0
    for _ in range(400):
        rid += 1
        # products 71..80 deliberately never reviewed
        reviews.append((rid, rng.randint(1, 70), rng.randint(1, 110),
                        rng.randint(1, 5), _d(rng.randint(0, DAYS)), None))
    cur.executemany("INSERT INTO reviews VALUES (?,?,?,?,?,?)", reviews)

    conn.commit()


if __name__ == "__main__":  # pragma: no cover
    import config

    conn = sqlite3.connect(config.DB_PATH)
    seed(conn)
    print("seeded", config.DB_PATH)
