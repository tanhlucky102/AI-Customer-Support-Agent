"""Issue a signing secret for an existing shop; deliver it to its backend securely."""

import argparse
import json
import secrets

from knowledge_store import init_db, set_chat_token_secret


def main() -> None:
    parser = argparse.ArgumentParser(description="Issue or rotate one shop's chat JWT secret")
    parser.add_argument("--shop-id", required=True)
    args = parser.parse_args()
    init_db()
    secret = secrets.token_urlsafe(32)
    if not set_chat_token_secret(args.shop_id, secret):
        parser.error("unknown shop-id")
    print(json.dumps({"shop_id": args.shop_id, "chat_token_secret": secret}, indent=2))


if __name__ == "__main__":
    main()
