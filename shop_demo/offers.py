"""Published shop offers and events used by checkout and the support API."""

import json
from datetime import datetime, timezone
from pathlib import Path


COUPONS = {
    "GIAM50K": "Giảm 50.000₫ cho đơn từ 500.000₫",
    "GIAM5": "Giảm 5% (tối đa 300.000₫)",
    "FREESHIP": "Miễn phí vận chuyển",
    "WELCOME100": "Giảm 100.000₫ cho đơn từ 2.000.000₫",
}
EVENTS_PATH = Path(__file__).with_name("events.json")
EVENT_BANNER_TITLE = "HOT SALE CUỐI TUẦN"


def coupon_discount(code: str, subtotal: int, fee: int) -> tuple[int, str | None]:
    if code == "GIAM50K":
        return (50_000, None) if subtotal >= 500_000 else (0, "Mã GIAM50K áp dụng cho đơn từ 500.000₫.")
    if code == "GIAM5":
        return min(subtotal * 5 // 100, 300_000), None
    if code == "FREESHIP":
        return (fee, None) if fee else (0, "Đơn của bạn đã được miễn phí vận chuyển.")
    if code == "WELCOME100":
        return (100_000, None) if subtotal >= 2_000_000 else (0, "Mã WELCOME100 áp dụng cho đơn từ 2.000.000₫.")
    return 0, "Mã giảm giá không hợp lệ."


def published_events() -> dict:
    """Empty/unavailable source means unknown schedule, never an asserted empty calendar."""
    try:
        entries = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))["events"]
        if not isinstance(entries, list):
            raise ValueError("events must be a list")
    except (OSError, ValueError, KeyError, TypeError):
        return {"events_status": "unavailable", "current_events": [], "upcoming_events": []}
    if not entries:
        return {"events_status": "not_configured", "current_events": [], "upcoming_events": []}
    now = datetime.now(timezone.utc)
    current, upcoming = [], []
    for row in entries:
        try:
            start = datetime.fromisoformat(row["starts_at"])
            end = datetime.fromisoformat(row["ends_at"])
            title, description = row["title"], row["description"]
            if (not start.tzinfo or not end.tzinfo or end <= start or
                    not isinstance(title, str) or not title.strip() or
                    not isinstance(description, str) or not description.strip()):
                raise ValueError("invalid event")
            event = {"title": title.strip()[:160],
                     "description": description.strip()[:1000],
                     "starts_at": start.isoformat(), "ends_at": end.isoformat(),
                     "url": row.get("url") if isinstance(row.get("url"), str) else None}
            if start <= now <= end:
                current.append(event)
            elif now < start:
                upcoming.append(event)
        except (KeyError, TypeError, ValueError):
            return {"events_status": "unavailable", "current_events": [], "upcoming_events": []}
    upcoming.sort(key=lambda item: item["starts_at"])
    return {"events_status": "configured", "current_events": current[:5],
            "upcoming_events": upcoming[:5]}
