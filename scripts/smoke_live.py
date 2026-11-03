"""Live smoke test across the running servers (scripts/run_all.py): python scripts/smoke_live.py

Uses the real agent (AI Box) when AI_BOX_API_KEY is set, so it checks facts, not exact wording.
"""

import json
import os

import httpx

AGENT_URL = os.getenv("SMOKE_AGENT_URL", "http://127.0.0.1:8000")
SHOP_URL = os.getenv("SMOKE_SHOP_URL", "http://127.0.0.1:8001")
SECOND_URL = os.getenv("SMOKE_SECOND_URL", "http://127.0.0.1:8003")


def ask(client: httpx.Client, message: str, token: str, shop_key: str = "pk_demo_shop") -> tuple[str, dict]:
    response = client.post("/v1/chat", json={"shop_key": shop_key, "message": message},
                           headers={"Authorization": f"Bearer {token}"})
    response.raise_for_status()
    text, events = "", {}
    for block in response.text.split("\n\n"):
        lines = block.splitlines()
        if len(lines) >= 2 and lines[0].startswith("event: "):
            name, data = lines[0][7:], json.loads(lines[1].removeprefix("data: "))
            events.setdefault(name, data)
            if name == "token":
                text += data["text"]
    assert "done" in events, response.text
    return text, events


def login(base: str, email: str) -> str:
    with httpx.Client(base_url=base, timeout=15, follow_redirects=True) as shop:
        assert shop.get("/api/chat-token").status_code == 401
        shop.post("/login", data={"email": email, "password": "demo1234", "next": "/"})
        return shop.get("/api/chat-token").json()["token"]


def main() -> None:
    with httpx.Client(base_url=AGENT_URL, timeout=90) as agent:
        print("agent mode:", agent.get("/healthz").json())
        assert agent.get("/widget/loader.js").status_code == 200
        assert agent.post("/v1/chat", json={"shop_key": "pk_demo_shop", "message": "hi"}).status_code == 401
        print("PASS: chat requires login")

        token = login(SHOP_URL, "an@demo.vn")
        reply, events = ask(agent, "Phí giao hàng cho đơn 300 nghìn là bao nhiêu?", token)
        assert "30.000" in reply, reply
        print("PASS: policy answer with citations:", [s["label"] for s in events.get("citations", {}).get("sources", [])])
        reply, _ = ask(agent, "Đơn SO10293 của tôi tới đâu rồi?", token)
        assert "GHTK6761334796" in reply, reply  # tracking code: only the order API knows it
        print("PASS: order lookup ->", reply[:120].replace("\n", " "))

        other = login(SHOP_URL, "binh@demo.vn")
        reply, _ = ask(agent, "Đơn SO10293 của tôi tới đâu rồi?", other)
        assert "26.024.000" not in reply, reply
        print("PASS: another customer cannot read SO10293 ->", reply[:100].replace("\n", " "))

        second = login(SECOND_URL, "chau@pkx.vn")
        reply, _ = ask(agent, "Đơn PKX204518 của tôi đến đâu rồi?", second, shop_key="pk_second_shop")
        # The tracking code exists only in the shop's API, never in its documents.
        assert "S22.81726354" in reply, reply
        print("PASS: second shop order through the JSON mapping ->", reply[:120].replace("\n", " "))


if __name__ == "__main__":
    main()
