"""Customer backend example: push a complete FAQ snapshot to ATI Agent Server."""

import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import httpx


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python examples/push_faq.py path/to/faqs.json")
    base_url = os.getenv("ATI_SERVER_URL", "http://localhost:8000").rstrip("/")
    shop_id = os.getenv("ATI_SHOP_ID")
    write_key = os.getenv("ATI_FAQ_WRITE_KEY")
    if not shop_id or not write_key:
        raise SystemExit("Set ATI_SHOP_ID and ATI_FAQ_WRITE_KEY on the website backend first")
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    url = f"{base_url}/v1/shops/{quote(shop_id, safe='')}/faqs"
    with httpx.Client(timeout=15) as client:
        response = client.put(url, json=payload,
                              headers={"Authorization": f"Bearer {write_key}"})
    response.raise_for_status()
    result = response.json()
    print(f"Synced {result['count']} FAQs for {result['shop_id']} (revision {result['revision']})")


if __name__ == "__main__":
    main()
