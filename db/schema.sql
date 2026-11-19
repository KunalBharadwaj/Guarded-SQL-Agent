-- Guarded NL-to-Action Agent — business schema (SQLite)
-- A production-shaped e-commerce schema: non-trivial joins, aggregations, and
-- enough "messiness" (nullable columns, status enums, soft relationships) to make
-- naive text-to-SQL fail without schema grounding.

PRAGMA foreign_keys = ON;

CREATE TABLE categories (
    category_id   INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    parent_id     INTEGER REFERENCES categories(category_id)  -- self-join: subcategories
);

CREATE TABLE suppliers (
    supplier_id   INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    country       TEXT,
    lead_time_days INTEGER                                     -- nullable on purpose
);

CREATE TABLE products (
    product_id    INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    category_id   INTEGER REFERENCES categories(category_id),
    supplier_id   INTEGER REFERENCES suppliers(supplier_id),
    unit_price    REAL NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1                   -- 1=active, 0=discontinued
);

CREATE TABLE inventory (
    product_id    INTEGER PRIMARY KEY REFERENCES products(product_id),
    quantity      INTEGER NOT NULL DEFAULT 0,
    reorder_level INTEGER NOT NULL DEFAULT 10,
    warehouse     TEXT
);

CREATE TABLE customers (
    customer_id   INTEGER PRIMARY KEY,
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    email         TEXT UNIQUE,
    country       TEXT,
    signup_date   TEXT NOT NULL,                               -- ISO date 'YYYY-MM-DD'
    is_active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE employees (
    employee_id   INTEGER PRIMARY KEY,
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    title         TEXT,
    hire_date     TEXT,
    manager_id    INTEGER REFERENCES employees(employee_id)    -- self-join: org chart
);

CREATE TABLE orders (
    order_id      INTEGER PRIMARY KEY,
    customer_id   INTEGER NOT NULL REFERENCES customers(customer_id),
    employee_id   INTEGER REFERENCES employees(employee_id),   -- sales rep, nullable
    order_date    TEXT NOT NULL,                               -- ISO date
    status        TEXT NOT NULL DEFAULT 'pending',             -- pending|shipped|delivered|cancelled|returned
    ship_country  TEXT
);

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders(order_id),
    product_id    INTEGER NOT NULL REFERENCES products(product_id),
    quantity      INTEGER NOT NULL,
    unit_price    REAL NOT NULL,                               -- price captured at sale time
    discount      REAL NOT NULL DEFAULT 0.0                    -- 0.0–1.0 fraction
);

CREATE TABLE payments (
    payment_id    INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders(order_id),
    amount        REAL NOT NULL,
    method        TEXT,                                        -- card|paypal|wire|gift_card
    paid_date     TEXT,                                        -- nullable: unpaid => NULL
    status        TEXT NOT NULL DEFAULT 'captured'             -- captured|refunded|failed
);

CREATE TABLE reviews (
    review_id     INTEGER PRIMARY KEY,
    product_id    INTEGER NOT NULL REFERENCES products(product_id),
    customer_id   INTEGER NOT NULL REFERENCES customers(customer_id),
    rating        INTEGER NOT NULL,                            -- 1–5
    review_date   TEXT,
    body          TEXT
);

CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_order_items_order ON order_items(order_id);
CREATE INDEX idx_order_items_product ON order_items(product_id);
CREATE INDEX idx_payments_order ON payments(order_id);
CREATE INDEX idx_reviews_product ON reviews(product_id);
