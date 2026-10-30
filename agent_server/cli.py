"""Chat with the agent core in a terminal, without the widget or HTTP.

    python -m agent_server.cli --shop demo_shop --customer C0001
    > đơn SO10293 tới đâu rồi
"""

import argparse
import asyncio
import uuid

from agent_server import agent
from agent_server.commerce import adapter_for
from agent_server.store import create_conversation, init_db, shop_by_id
from agent_server.tools import TurnContext
from rag.search import search


async def chat(shop: dict, customer_id: str) -> None:
    conversation_id = uuid.uuid4().hex
    create_conversation(conversation_id, shop["shop_id"], customer_id, "CLI session")
    history: list[dict] = []
    while True:
        try:
            message = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not message:
            continue
        ctx = TurnContext(shop=shop, customer_id=customer_id, conversation_id=conversation_id,
                          turn_key=uuid.uuid4().hex, adapter=adapter_for(shop), search=search)
        async for event in agent.run(ctx, history, message):
            if event["type"] == "status":
                print(f"  [{event['message']}]")
            elif event["type"] == "token":
                print(event["text"], end="", flush=True)
            elif event["type"] == "done":
                print()
                for source in event["sources"]:
                    print(f"  - {source.get('ref', '')} {source['label']}: {source['href']}")
                print(f"  ({event['mode']}, tools={[call['name'] for call in event['tools']]}, "
                      f"llm_calls={event['llm_calls']}, tokens={event['usage']})")
                history += [{"role": "user", "content": message},
                            {"role": "assistant", "content": event["reply"]}]
            elif event["type"] == "error":
                print("\n  ERROR:", event["message"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Terminal chat with the agent core")
    parser.add_argument("--shop", default="demo_shop")
    parser.add_argument("--customer", default="C0001", help="Plays the role of the verified JWT sub")
    args = parser.parse_args()
    init_db()
    shop = shop_by_id(args.shop)
    if not shop:
        parser.error("unknown shop")
    asyncio.run(chat(shop, args.customer))


if __name__ == "__main__":
    main()
