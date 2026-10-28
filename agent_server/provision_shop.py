"""Operator-side CLI: create a shop and print its integration keys once."""

import argparse
import json
import re
import secrets
import sqlite3

from knowledge_store import create_shop, init_db, origin_of


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision one customer website for the widget")
    parser.add_argument("--shop-id", required=True, help="Stable slug, e.g. sample_store")
    parser.add_argument("--shop-url", required=True, help="Public website URL")
    parser.add_argument("--origin", action="append", default=[], help="Allowed embed origin; repeatable")
    parser.add_argument("--title", required=True, help="Widget title")
    parser.add_argument("--greeting", default="Xin chào! Tôi có thể giúp gì cho bạn?")
    parser.add_argument("--color", default="#2563eb")
    parser.add_argument("--suggestion", action="append", default=[])
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

    public_key = "pk_" + secrets.token_urlsafe(16)
    faq_write_key = "sk_" + secrets.token_urlsafe(32)
    chat_token_secret = secrets.token_urlsafe(32)
    init_db()
    try:
        create_shop(shop_id=args.shop_id, public_key=public_key, faq_write_key=faq_write_key,
                    title=args.title, greeting=args.greeting, primary_color=args.color,
                    shop_url=args.shop_url, allowed_origins=origins,
                    chat_token_secret=chat_token_secret, suggestions=args.suggestion)
    except sqlite3.IntegrityError:
        parser.error("shop-id already exists; choose a new one")
    print(json.dumps({"shop_id": args.shop_id, "public_key": public_key,
                      "faq_write_key": faq_write_key, "chat_token_secret": chat_token_secret,
                      "allowed_origins": origins},
                     ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
