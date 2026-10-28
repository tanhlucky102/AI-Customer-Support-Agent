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


if __name__ == "__main__":
    unittest.main()
