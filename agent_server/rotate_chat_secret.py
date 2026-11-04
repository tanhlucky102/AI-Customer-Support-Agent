"""Issue a signing secret for an existing shop; deliver it to its backend securely.

    python -m agent_server.rotate_chat_secret --shop-id sample_store
"""

import argparse
import json
import secrets

from agent_server.store import init_db, set_chat_token_secret


def main() -> None:
    parser = argparse.ArgumentParser(description="Issue or rotate one shop's chat JWT secret")
    parser.add_argument("--shop-id", required=True)
    args = parser.parse_args()
    from agent_server.store import DEMO_DATA, demo_shop_enabled
    if args.shop_id == DEMO_DATA["shop_id"] and demo_shop_enabled():
        parser.error("the demo shop's secret comes from .env (DEMO_SHOP_TOKEN_SECRET), shared with the "
                     "demo website; change it there and restart both servers")
    init_db()
    secret = secrets.token_urlsafe(32)
    if not set_chat_token_secret(args.shop_id, secret):
        parser.error("unknown shop-id")
    print(json.dumps({"shop_id": args.shop_id, "chat_token_secret": secret}, indent=2))


if __name__ == "__main__":
    main()
