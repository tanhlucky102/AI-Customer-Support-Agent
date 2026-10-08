"""Sinh dữ liệu giả: 300 khách, 200 sản phẩm, 1.000 đơn hàng.

Chạy:  python seed.py [--reset]
Tài khoản demo (mật khẩu demo1234): an@demo.vn, binh@demo.vn
Đơn SO10293 thuộc về An (dùng cho câu hỏi mẫu "đơn SO10293 tới đâu rồi").
"""
import random
import sys
from datetime import datetime, timedelta

from config import DB_PATH
from db import connect, init_db
from security import hash_password

random.seed(42)
NOW = datetime(2026, 10, 8, 12, 0)
DEMO_PASSWORD = "demo1234"

HO = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ", "Võ", "Đặng", "Bùi", "Đỗ", "Hồ", "Ngô", "Dương"]
DEM = ["Văn", "Thị", "Minh", "Quốc", "Thanh", "Ngọc", "Hữu", "Anh", "Gia", "Thu", "Đức", "Khánh"]
TEN = ["An", "Bình", "Châu", "Dũng", "Giang", "Hà", "Hùng", "Khoa", "Lan", "Linh", "Long", "Mai", "Nam",
       "Phong", "Quang", "Sơn", "Thảo", "Trang", "Tú", "Việt", "Yến", "Huy", "Nhi", "Đạt"]
QUAN = ["Quận 1", "Quận 3", "Quận 7", "Bình Thạnh", "Thủ Đức", "Cầu Giấy", "Đống Đa", "Hai Bà Trưng",
        "Hải Châu", "Ninh Kiều"]
TINH = {"Quận": "TP. Hồ Chí Minh", "Bình": "TP. Hồ Chí Minh", "Thủ": "TP. Hồ Chí Minh",
        "Cầu": "Hà Nội", "Đống": "Hà Nội", "Hai": "Hà Nội", "Hải": "Đà Nẵng", "Ninh": "Cần Thơ"}

# Điện thoại: (hãng, mẫu, giá cơ sở bản thấp nhất, các bản bộ nhớ)
PHONES = [
    ("Apple", "iPhone 18 Pro", 36_000_000, [256, 512, 1024]), ("Apple", "iPhone 17 Pro", 33_000_000, [256, 512, 1024]),
    ("Apple", "iPhone 17", 24_000_000, [256, 512]), ("Samsung", "Galaxy S26 Ultra", 31_000_000, [256, 512, 1024]),
    ("Samsung", "Galaxy Z Fold8", 44_000_000, [256, 512, 1024]), ("OPPO", "Reno16 F", 9_500_000, [256, 512]),
    ("Apple", "iPhone 16 Pro Max", 30_000_000, [256, 512, 1024]), ("Apple", "iPhone 16 Pro", 27_000_000, [128, 256, 512]),
    ("Apple", "iPhone 16", 22_000_000, [128, 256, 512]), ("Apple", "iPhone 15", 17_000_000, [128, 256]),
    ("Apple", "iPhone 14", 13_500_000, [128, 256]),
    ("Samsung", "Galaxy S25 Ultra", 28_000_000, [256, 512, 1024]), ("Samsung", "Galaxy S25+", 22_000_000, [256, 512]),
    ("Samsung", "Galaxy S25", 19_000_000, [128, 256]), ("Samsung", "Galaxy Z Fold6", 40_000_000, [256, 512]),
    ("Samsung", "Galaxy Z Flip6", 25_000_000, [256, 512]), ("Samsung", "Galaxy A56 5G", 10_500_000, [128, 256]),
    ("Samsung", "Galaxy A36 5G", 8_500_000, [128, 256]), ("Samsung", "Galaxy A16", 5_000_000, [128]),
    ("Xiaomi", "Xiaomi 15 Ultra", 25_000_000, [256, 512]), ("Xiaomi", "Xiaomi 14T", 12_000_000, [256, 512]),
    ("Xiaomi", "Redmi Note 14 Pro", 7_500_000, [128, 256]), ("Xiaomi", "Redmi 14C", 3_200_000, [64, 128]),
    ("Xiaomi", "POCO F7", 11_000_000, [256, 512]),
    ("OPPO", "Find X8", 20_000_000, [256, 512]), ("OPPO", "Reno13 F", 8_500_000, [128, 256]),
    ("OPPO", "Reno13 Pro", 15_000_000, [256, 512]), ("OPPO", "A5 Pro", 5_500_000, [128, 256]),
    ("vivo", "vivo X200 Pro", 23_000_000, [256, 512]), ("vivo", "vivo V50", 11_500_000, [256, 512]),
    ("vivo", "vivo Y29", 4_800_000, [128, 256]),
    ("realme", "realme GT 7 Pro", 16_000_000, [256, 512]), ("realme", "realme 14 Pro+", 10_000_000, [256, 512]),
    ("realme", "realme C75", 4_300_000, [128, 256]),
    ("HONOR", "HONOR Magic7 Pro", 24_000_000, [256, 512]), ("HONOR", "HONOR 400", 9_500_000, [256, 512]),
]
COLORS = ["Đen", "Trắng", "Xanh dương", "Hồng", "Tím", "Vàng", "Xanh lá", "Titan"]
PHONE_DESC = "Điện thoại chính hãng, bảo hành 12 tháng, 1 đổi 1 trong 30 ngày nếu lỗi nhà sản xuất. Hỗ trợ trả góp 0%."

# Phụ kiện: (danh mục, hãng, tên, khoảng giá)
ACCESSORIES = {
    "Ốp lưng": (["Spigen", "UAG", "Baseus", "Ugreen"], ["Ốp lưng chống sốc", "Ốp lưng trong suốt", "Ốp lưng MagSafe"], (90_000, 650_000)),
    "Sạc & cáp": (["Anker", "Baseus", "Belkin", "Ugreen"], ["Sạc nhanh 20W", "Sạc nhanh 65W GaN", "Cáp USB-C 1m", "Cáp Lightning 2m", "Sạc không dây 15W"], (120_000, 1_200_000)),
    "Tai nghe": (["Sony", "JBL", "Apple", "Samsung", "Xiaomi"], ["Tai nghe Bluetooth True Wireless", "Tai nghe chụp tai chống ồn", "Tai nghe thể thao"], (350_000, 6_500_000)),
    "Pin dự phòng": (["Anker", "Xiaomi", "Baseus", "Romoss"], ["Pin dự phòng 10.000mAh", "Pin dự phòng 20.000mAh", "Pin dự phòng 20.000mAh sạc nhanh"], (250_000, 1_500_000)),
    "Dán màn hình": (["Spigen", "Baseus", "Nillkin"], ["Kính cường lực", "Dán chống nhìn trộm", "Dán nhám chống vân tay"], (60_000, 350_000)),
}
ACC_DESC = "Phụ kiện chính hãng, bảo hành 6 tháng, đổi mới trong 15 ngày nếu lỗi."

STATUS_FLOW = ["pending", "confirmed", "shipping", "delivered"]
CARRIERS = ["GHN", "GHTK", "VNP", "JT"]


def phone() -> str:
    return "0" + random.choice("93578") + "".join(random.choices("0123456789", k=8))


def address() -> str:
    q = random.choice(QUAN)
    return f"{random.randint(1, 250)} Đường {random.choice(['Lê Lợi', 'Nguyễn Huệ', 'Trần Hưng Đạo', 'Hai Bà Trưng', 'Lý Thường Kiệt'])}, {q}, {TINH.get(q.split()[0], 'Việt Nam')}"


def strip_accents(s: str) -> str:
    import unicodedata
    s = s.replace("Đ", "D").replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


RAM_BY_STORAGE = {64: 4, 128: 6, 256: 8, 512: 12, 1024: 16}


def seed_products(conn) -> list[tuple[int, int]]:
    rows, n = [], 0
    phone_rows = []
    for brand, model, base, mems in PHONES:
        for mem in mems:
            price = base + mems.index(mem) * max(1_500_000, round(base * 0.1, -5))
            for color in random.sample(COLORS, 2):
                phone_rows.append((brand, model, mem, color, price))
    random.shuffle(phone_rows)
    for brand, model, mem, color, price in phone_rows[:130]:
        n += 1
        label = f"{model} {mem}GB" if mem < 1024 else f"{model} 1TB"
        sale = round(price * random.uniform(0.95, 1.0), -4)
        old = round(sale * random.choice([1.0, 1.05, 1.08, 1.12, 1.18, 1.25]), -4)
        ram = RAM_BY_STORAGE[mem] if brand != "Apple" else (6 if mem <= 256 else 8)
        screen = "7.6" if "Fold" in model else ("6.7" if "Flip" in model else random.choice(["6.1", "6.4", "6.7", "6.9"]))
        rows.append((f"SKU{n:04d}", f"{label} - {color}", brand, "Điện thoại", PHONE_DESC, sale, old,
                     "iOS" if brand == "Apple" else "Android", ram, mem, screen,
                     round(random.uniform(4.3, 5.0), 1), random.randint(20, 4000), random.randint(0, 60)))
    per_cat = 70 // len(ACCESSORIES)
    for cat, (brands, items, (lo, hi)) in ACCESSORIES.items():
        for _ in range(per_cat):
            n += 1
            b = random.choice(brands)
            sale = round(random.randint(lo, hi), -3)
            old = round(sale * random.choice([1.0, 1.1, 1.2, 1.3]), -3)
            name = f"{random.choice(items)} {b} {random.choice(['', 'Pro', 'Plus', 'Mini', 'Lite'])}".strip()
            rows.append((f"SKU{n:04d}", f"{name} {random.choice(COLORS)}", b, cat, ACC_DESC, sale, old, None, None,
                         None, None, round(random.uniform(4.2, 5.0), 1), random.randint(10, 3000),
                         random.randint(0, 150)))
    conn.executemany(
        "INSERT INTO products(sku,name,brand,category,description,price,old_price,os,ram_gb,storage_gb,screen,"
        "rating,sold,stock) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return [(r["id"], r["price"]) for r in conn.execute("SELECT id, price FROM products")]


def seed_customers(conn) -> list[str]:
    shared_hash = hash_password(DEMO_PASSWORD)  # băm một lần, dùng chung để seed nhanh
    rows = [
        ("C0001", "an@demo.vn", shared_hash, "Nguyễn Văn An", "0901234567",
         "12 Đường Lê Lợi, Quận 1, TP. Hồ Chí Minh", "gold", "2025-03-15"),
        ("C0002", "binh@demo.vn", shared_hash, "Trần Thị Bình", "0912345678",
         "45 Đường Trần Hưng Đạo, Cầu Giấy, Hà Nội", "standard", "2025-06-02"),
    ]
    used = {"an@demo.vn", "binh@demo.vn"}
    for n in range(3, 301):
        name = f"{random.choice(HO)} {random.choice(DEM)} {random.choice(TEN)}"
        email = f"{strip_accents(name).lower().replace(' ', '.')}{n}@example.com"
        if email in used:
            continue
        used.add(email)
        created = (NOW - timedelta(days=random.randint(30, 700))).strftime("%Y-%m-%d")
        rows.append((f"C{n:04d}", email, shared_hash, name, phone(), address(),
                     random.choices(["standard", "silver", "gold"], [70, 22, 8])[0], created))
    conn.executemany(
        "INSERT INTO customers(id,email,password_hash,full_name,phone,address,tier,created_at) VALUES (?,?,?,?,?,?,?,?)",
        rows)
    return [r[0] for r in rows]


def shipping_fee(subtotal: int) -> int:
    return 0 if subtotal >= 500_000 else 30_000


def seed_orders(conn, customers: list[str], products: list[tuple[int, int]]) -> None:
    info = {r["id"]: r for r in conn.execute("SELECT id, full_name, phone, address FROM customers")}
    for n in range(1000):
        oid = f"SO{10001 + n}"
        cid = "C0001" if oid == "SO10293" else random.choice(customers)
        c = info[cid]
        placed = NOW - timedelta(days=random.randint(0, 120), hours=random.randint(0, 23))
        if oid == "SO10293":  # đơn mẫu: đang giao, trễ 5 ngày so với dự kiến
            placed = NOW - timedelta(days=12)
        age = (NOW - placed).days
        # Đơn càng cũ thì càng có khả năng đã giao
        if oid == "SO10293":
            status = "shipping"
        elif age > 10:
            status = random.choices(["delivered", "cancelled", "returned"], [86, 9, 5])[0]
        elif age > 4:
            status = random.choices(["shipping", "delivered", "cancelled"], [40, 55, 5])[0]
        else:
            status = random.choices(["pending", "confirmed", "shipping", "cancelled"], [35, 30, 30, 5])[0]
        picked = random.sample(products, random.randint(1, 3))
        items = [(pid, random.randint(1, 2), price) for pid, price in picked]
        sub = sum(q * p for _, q, p in items)
        fee = shipping_fee(sub)
        eta = placed + timedelta(days=random.randint(3, 5))
        if oid == "SO10293":
            eta = NOW - timedelta(days=5)
        tracking = None
        if status in ("shipping", "delivered", "returned"):
            tracking = f"{random.choice(CARRIERS)}{random.randint(10**9, 10**10 - 1)}"
        conn.execute(
            "INSERT INTO orders(id,customer_id,status,total,shipping_fee,payment_method,ship_name,ship_phone,"
            "ship_address,placed_at,eta,tracking_code) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, cid, status, sub + fee, fee, random.choice(["cod", "bank"]), c["full_name"], c["phone"],
             c["address"], placed.strftime("%Y-%m-%d %H:%M"), eta.strftime("%Y-%m-%d"), tracking))
        conn.executemany("INSERT INTO order_items(order_id,product_id,qty,unit_price) VALUES (?,?,?,?)",
                         [(oid, pid, q, p) for pid, q, p in items])


def main() -> None:
    if "--reset" in sys.argv and DB_PATH.exists():
        DB_PATH.unlink()
    init_db()
    with connect() as conn:
        if conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]:
            print("DB đã có dữ liệu. Dùng --reset để tạo lại.")
            return
        products = seed_products(conn)
        customers = seed_customers(conn)
        seed_orders(conn, customers, products)
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("customers", "products", "orders", "order_items")}
    print("Đã seed:", counts)
    print(f"Đăng nhập demo: an@demo.vn / {DEMO_PASSWORD}  (có đơn SO10293)")


if __name__ == "__main__":
    main()
