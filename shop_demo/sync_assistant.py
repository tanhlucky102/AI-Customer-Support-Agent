"""Publish this shop's promotions and dated events to the Agent Server (offers snapshot).

The chatbot answers promotion questions from this snapshot, never by calling the shop per question.
Run after editing events.json:   python sync_assistant.py      (in shop_demo/)
"""

import json
import os
from pathlib import Path

import httpx

import config
from offers import EVENT_BANNER_TITLE, EVENTS_PATH


def snapshot() -> dict:
    offers = [{
        "id": "hot-sale-cuoi-tuan", "kind": "promotion", "title": EVENT_BANNER_TITLE.title(),
        "description": "Giá giảm cho nhiều điện thoại và phụ kiện vào thứ Bảy và Chủ nhật; giá cũ hiển "
                       "gạch ngang trên trang sản phẩm. Điều kiện chi tiết xem trang Hot sale cuối tuần.",
        "source_url": f"{config.SHOP_URL}/help/hot-sale-cuoi-tuan"}]
    events = json.loads(Path(EVENTS_PATH).read_text(encoding="utf-8"))["events"]
    for index, event in enumerate(events):
        url = event.get("url") or "/"
        offers.append({"id": f"event-{index + 1}", "kind": "event", "title": event["title"],
                       "description": event["description"], "starts_at": event["starts_at"],
                       "ends_at": event["ends_at"],
                       "source_url": url if url.startswith("http") else config.SHOP_URL + url})
    return {"offers": offers}


def main() -> None:
    write_key = os.getenv("DEMO_FAQ_WRITE_KEY")
    if not write_key:
        raise SystemExit("Set DEMO_FAQ_WRITE_KEY in .env (the demo shop's content write key)")
    response = httpx.put(f"{config.AGENT_SERVER_INTERNAL_URL}/v1/shops/demo_shop/offers", json=snapshot(),
                         headers={"Authorization": f"Bearer {write_key}"}, timeout=15)
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    main()
