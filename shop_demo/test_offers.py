"""Published event schedules must never turn missing data into a confirmed claim."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import offers


class PublishedEventsTest(unittest.TestCase):
    def test_empty_invalid_and_dated_events(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.json"
            with patch.object(offers, "EVENTS_PATH", path):
                path.write_text('{"events": []}', encoding="utf-8")
                self.assertEqual(offers.published_events()["events_status"], "not_configured")

                now = datetime.now(timezone.utc)
                event = {"title": "Giới thiệu sản phẩm", "description": "Tại cửa hàng",
                         "starts_at": (now + timedelta(days=1)).isoformat(),
                         "ends_at": (now + timedelta(days=2)).isoformat(), "url": "/events/new"}
                path.write_text(json.dumps({"events": [event]}), encoding="utf-8")
                result = offers.published_events()
                self.assertEqual(result["events_status"], "configured")
                self.assertEqual(result["upcoming_events"][0]["title"], event["title"])

                event["ends_at"] = (now - timedelta(days=1)).isoformat()
                path.write_text(json.dumps({"events": [event]}), encoding="utf-8")
                self.assertEqual(offers.published_events()["events_status"], "unavailable")

                event["starts_at"] = (now - timedelta(days=3)).isoformat()
                path.write_text(json.dumps({"events": [event]}), encoding="utf-8")
                result = offers.published_events()
                self.assertEqual(result["events_status"], "configured")
                self.assertEqual(result["current_events"], [])
                self.assertEqual(result["upcoming_events"], [])



class EventDiscountTest(unittest.TestCase):
    def line(self, name, brand, category, amount):
        return {"product": {"name": name, "brand": brand, "category": category}, "qty": 1, "amount": amount}

    def test_the_published_rules_are_what_checkout_charges(self):
        cart = [self.line("Galaxy S25 256GB", "Samsung", "Điện thoại", 30_000_000),
                self.line("Ốp lưng iPhone", "Apple", "Ốp lưng", 200_000)]
        samsung_week = datetime(2026, 10, 30, tzinfo=timezone.utc)
        by_card = offers.event_discount(cart, "cod", samsung_week)
        self.assertEqual((by_card["amount"], by_card["hints"]), (0, ["Tuần lễ Samsung Galaxy"]))
        by_bank = offers.event_discount(cart, "bank", samsung_week)
        self.assertEqual(by_bank["amount"], 1_000_000)  # 5% of 30M is 1.5M, capped at 1M
        self.assertTrue(by_bank["gifts"])
        sale = offers.event_discount(cart, "cod", datetime(2026, 11, 11, 5, tzinfo=timezone.utc))
        self.assertEqual(sale["amount"], 40_000)  # 20% of the accessory only
        self.assertTrue(sale["free_shipping"])
        self.assertEqual(offers.event_discount(cart, "bank", datetime(2026, 11, 4, tzinfo=timezone.utc))["amount"], 0)


if __name__ == "__main__":
    unittest.main()
