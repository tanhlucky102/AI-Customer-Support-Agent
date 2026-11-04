"""The website's pages through its routers, on a freshly seeded database (run from shop_demo/)."""

import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import db
import offers
import seed

TMP = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows keeps an open SQLite file
db.DB_PATH = seed.DB_PATH = Path(TMP.name) / "shop.db"

from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402

SAMSUNG_WEEK = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def during_samsung_week(lines, payment_method):
    return offers.event_discount(lines, payment_method, SAMSUNG_WEEK)


class PagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.main()
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def login(self):
        response = self.client.post("/login", data={"email": "an@demo.vn", "password": "demo1234", "next": "/"},
                                    follow_redirects=False)
        self.assertEqual(response.status_code, 303)

    def test_public_pages_and_old_policy_links(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/products/1").status_code, 200)
        self.assertEqual(self.client.get("/help/tra-gop").status_code, 200)
        moved = self.client.get("/policy/doi-tra", follow_redirects=False)
        self.assertEqual((moved.status_code, moved.headers["location"]), (301, "/help/doi-tra"))
        self.assertEqual(self.client.get("/no-such-page").status_code, 404)
        self.assertNotIn("demo-chat", self.client.get("/").text)  # the old rule chatbot is gone

    def test_checkout_charges_the_published_event_and_records_delivery(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/chat-token").status_code, 401)
        self.login()
        self.assertIn("token", self.client.get("/api/chat-token").json())
        with db.connect() as conn:
            phone = conn.execute("SELECT id, price FROM products WHERE brand = 'Samsung' AND name LIKE 'Galaxy S%' "
                                 "AND stock > 0 ORDER BY price DESC LIMIT 1").fetchone()
        self.client.post("/cart/add", data={"product_id": phone["id"], "qty": 1}, follow_redirects=False)
        with patch("web.event_discount", during_samsung_week):
            page = self.client.get("/checkout").text
            self.assertIn("giảm thêm khi thanh toán chuyển khoản", page)  # a hint for COD
            response = self.client.post("/checkout", follow_redirects=False, data={
                "ship_name": "An", "ship_phone": "0901234567", "ship_address": "12 Lê Lợi",
                "payment_method": "bank", "checkout_key": "k-test-1"})
        order_id = re.search(r"/orders/(SO\d+)", response.headers["location"]).group(1)
        expected = min(phone["price"] * 5 // 100, 1_000_000)
        with db.connect() as conn:
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        self.assertEqual(order["discount"], expected)
        self.assertEqual(order["total"], phone["price"] + order["shipping_fee"] - expected)
        for _ in range(3):  # [Demo] pending -> confirmed -> shipping -> delivered
            self.client.post(f"/orders/{order_id}/advance", follow_redirects=False)
        with db.connect() as conn:
            order = conn.execute("SELECT status, delivered_at FROM orders WHERE id = ?", (order_id,)).fetchone()
        self.assertEqual(order["status"], "delivered")
        self.assertEqual(order["delivered_at"], datetime.now().strftime("%Y-%m-%d"))
        api = self.client.get(f"/api/v1/customers/C0001/orders/{order_id}",
                              headers={"Authorization": f"Bearer {seed_api_key()}"})
        self.assertEqual(api.json()["delivered_at"], order["delivered_at"])  # what the assistant reads
        self.assertEqual(self.client.post("/logout", follow_redirects=False).status_code, 303)


def seed_api_key() -> str:
    import config
    return config.API_KEY


if __name__ == "__main__":
    unittest.main()
