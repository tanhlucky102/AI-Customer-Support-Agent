"""SQLite: schema và kết nối. Bảng khớp mục 4.4 của báo cáo."""
import sqlite3
from collections.abc import Iterator

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    phone TEXT,
    address TEXT,
    tier TEXT NOT NULL DEFAULT 'standard',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    brand TEXT,
    category TEXT NOT NULL,
    description TEXT,
    price INTEGER NOT NULL,
    old_price INTEGER,
    os TEXT,
    ram_gb INTEGER,
    storage_gb INTEGER,
    screen TEXT,
    rating REAL,
    sold INTEGER NOT NULL DEFAULT 0,
    stock INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    status TEXT NOT NULL,
    total INTEGER NOT NULL,
    shipping_fee INTEGER NOT NULL DEFAULT 0,
    payment_method TEXT NOT NULL DEFAULT 'cod',
    ship_name TEXT,
    ship_phone TEXT,
    ship_address TEXT,
    placed_at TEXT NOT NULL,
    eta TEXT,
    tracking_code TEXT,
    discount INTEGER NOT NULL DEFAULT 0,
    coupon_code TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id, placed_at DESC);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty INTEGER NOT NULL,
    unit_price INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS wishlist (
    customer_id TEXT NOT NULL REFERENCES customers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    PRIMARY KEY (customer_id, product_id)
);
CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        cols = {r['name'] for r in conn.execute('PRAGMA table_info(orders)')}  # nâng cấp DB cũ
        if 'discount' not in cols:
            conn.execute('ALTER TABLE orders ADD COLUMN discount INTEGER NOT NULL DEFAULT 0')
        if 'coupon_code' not in cols:
            conn.execute('ALTER TABLE orders ADD COLUMN coupon_code TEXT')


def get_db() -> Iterator[sqlite3.Connection]:
    """Dependency FastAPI: một kết nối cho mỗi request."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
