"""Operator-side CLI: create a shop and print its integration keys once.

    python -m agent_server.provision_shop --shop-id sample_store --shop-url https://shop.example.vn \
        --title "Sample Store assistant"                                   # level 0: content only
    python -m agent_server.provision_shop ... --commerce-config shop_api.json --commerce-api-key KEY
                                                                           # level 1: + order API
"""

import argparse
import json
import re
import secrets
from pathlib import Path

import psycopg

from agent_server.commerce import CAPABILITIES, config_problems, resolve_config
from agent_server.store import create_shop, init_db, origin_of


def load_commerce_config(path: str, parser: argparse.ArgumentParser) -> dict:
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        parser.error(f"cannot read commerce config: {error}")
    if not origin_of(config.get("base_url", "")):
        parser.error("commerce config needs base_url, an http(s) URL without credentials")
    operations = resolve_config(config)["operations"]
    unknown = set(operations) - set(CAPABILITIES)
    if unknown:
        parser.error(f"unknown operations {sorted(unknown)}; allowed: {list(CAPABILITIES)}")
    if not operations:
        parser.error("commerce config declares no operations")
    problems = config_problems(config)
    if problems:
        parser.error("unsafe commerce config:\n  " + "\n  ".join(problems))
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision one customer website for the widget")
    parser.add_argument("--shop-id", required=True, help="Stable slug, e.g. sample_store")
    parser.add_argument("--shop-url", required=True, help="Public website URL")
    parser.add_argument("--origin", action="append", default=[], help="Allowed embed origin; repeatable")
    parser.add_argument("--title", required=True, help="Widget title")
    parser.add_argument("--greeting", default="Xin chào! Tôi có thể giúp gì cho bạn?")
    parser.add_argument("--color", default="#2563eb")
    parser.add_argument("--suggestion", action="append", default=[])
    parser.add_argument("--tone", default="", help="Reply tone for the system prompt")
    parser.add_argument("--commerce-config", help="JSON mapping of the shop's order API (level 1)")
    parser.add_argument("--commerce-api-key", help="Server-to-server key for that API")
    parser.add_argument("--chat-token-secret", help="Reuse an existing secret instead of a new one")
    parser.add_argument("--faq-write-key", help="Reuse an existing write key instead of a new one")
    args = parser.parse_args()

    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", args.shop_id):
        parser.error("shop-id must be 3-64 lowercase letters/digits/_/- and start with a letter")
    shop_origin = origin_of(args.shop_url)
    if not shop_origin:
        parser.error("shop-url must be an http(s) URL without embedded credentials")
    origins = sorted({shop_origin, *(origin_of(item) for item in args.origin)})
    if "" in origins:
        parser.error("every --origin must be an http(s) origin")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", args.color):
        parser.error("color must be a 6-digit hex color")
    commerce = load_commerce_config(args.commerce_config, parser) if args.commerce_config else None
    if commerce and not args.commerce_api_key:
        parser.error("--commerce-config needs --commerce-api-key")

    public_key = "pk_" + secrets.token_urlsafe(16)
    faq_write_key = args.faq_write_key or "sk_" + secrets.token_urlsafe(32)
    chat_token_secret = args.chat_token_secret or secrets.token_urlsafe(32)
    init_db()
    try:
        create_shop(shop_id=args.shop_id, public_key=public_key, faq_write_key=faq_write_key,
                    title=args.title, greeting=args.greeting, primary_color=args.color,
                    shop_url=args.shop_url, allowed_origins=origins,
                    chat_token_secret=chat_token_secret, suggestions=args.suggestion,
                    tone=args.tone, commerce_config=commerce, commerce_api_key=args.commerce_api_key)
    except psycopg.errors.UniqueViolation:
        parser.error("shop-id already exists; choose a new one")
    capabilities = sorted(resolve_config(commerce)["operations"]) if commerce else []
    print(json.dumps({"shop_id": args.shop_id, "public_key": public_key,
                      "faq_write_key": faq_write_key, "chat_token_secret": chat_token_secret,
                      "allowed_origins": origins, "integration_level": 1 if commerce else 0,
                      "commerce_capabilities": capabilities},
                     ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
