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


def _events() -> list[dict]:
    try:
        entries = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))["events"]
        return entries if isinstance(entries, list) else []
    except (OSError, ValueError, KeyError, TypeError):
        return []


def event_discount(lines: list[dict], payment_method: str | None, now: datetime | None = None) -> dict:
    """What the events running now take off this cart, from each event's "rule": a percentage on the
    matching products (by brand and/or category, optionally capped and only for one payment method),
    free shipping, and gifts. A rule that needs another payment method is returned as a hint."""
    now = now or datetime.now(timezone.utc)
    result = {"amount": 0, "free_shipping": False, "titles": [], "hints": [], "gifts": []}
    for event in _events():
        rule = event.get("rule") or {}
        try:
            if not datetime.fromisoformat(event["starts_at"]) <= now <= datetime.fromisoformat(event["ends_at"]):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        matching = [line for line in lines
                    if (not rule.get("brand") or line["product"]["brand"] == rule["brand"])
                    and (not rule.get("categories") or line["product"]["category"] in rule["categories"])]
        if rule.get("payment") and payment_method != rule["payment"]:
            if matching:
                result["hints"].append(event["title"])
            continue
        amount = sum(line["amount"] for line in matching) * int(rule.get("percent", 0)) // 100
        if rule.get("max_discount"):
            amount = min(amount, int(rule["max_discount"]))
        gift = rule.get("gift") or {}
        gifts = [gift["text"] + " (" + line["product"]["name"] + ")" for line in matching
                 if any(line["product"]["name"].startswith(prefix) for prefix in gift.get("name_prefixes", []))]
        if amount or gifts or (rule.get("free_shipping") and lines):
            result["titles"].append(event["title"])
        result["amount"] += amount
        result["gifts"] += gifts
        result["free_shipping"] = result["free_shipping"] or bool(rule.get("free_shipping") and lines)
    return result


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
