"""Run against locally running shop (8001) and widget server (8000)."""

import os
import json

import httpx


AGENT_URL = os.getenv("SMOKE_AGENT_URL", "http://127.0.0.1:8000")
SHOP_URL = os.getenv("SMOKE_SHOP_URL", "http://127.0.0.1:8001")


def ask(client: httpx.Client, message: str, token: str | None = None) -> str:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.post("/v1/chat", json={"shop_key": "pk_demo_shop", "message": message},
                           headers=headers)
    response.raise_for_status()
    assert "event: done" in response.text, response.text
    parts = []
    for block in response.text.split("\n\n"):
        lines = block.splitlines()
        if lines and lines[0] == "event: token":
            parts.append(json.loads(lines[1].removeprefix("data: "))["text"])
    return "".join(parts)


def main() -> None:
    with httpx.Client(base_url=AGENT_URL, timeout=45) as agent:
        assert agent.get("/healthz").json()["mode"] in ("mock", "llm")
        assert agent.get("/widget/loader.js").status_code == 200
        assert agent.get("/widget/frame", params={
            "shop_key": "pk_demo_shop", "parent_origin": SHOP_URL
        }).status_code == 200
        guest = agent.post("/v1/chat", json={
            "shop_key": "pk_demo_shop", "message": "Phí giao hàng là bao nhiêu?"
        })
        assert guest.status_code == 401
        print("PASS: guest chat requires login")

        with httpx.Client(base_url=SHOP_URL, timeout=15, follow_redirects=True) as shop:
            page = shop.get("/")
            assert page.status_code == 200
            assert "/widget/loader.js" in page.text
            assert shop.get("/api/chat-token").status_code == 401
            login = shop.post("/login", data={"email": "an@demo.vn",
                                               "password": "demo1234", "next": "/"})
            assert login.status_code == 200, login.status_code
            jwt = shop.get("/api/chat-token").json()["token"]
            assert "30.000₫" in ask(agent, "Phí giao hàng là bao nhiêu?", jwt)
            result = ask(agent, "Đơn SO10293 của tôi", jwt)
            assert "SO10293" in result and "Mã vận đơn" in result, result
            print("PASS: logged-in order lookup across both servers")

            greeting = ask(agent, "Chào shop", jwt)
            assert "Chào bạn" in greeting and "sản phẩm" in greeting, greeting
            sale = ask(agent, "Shop đang có sale gì không?", jwt)
            assert "GIAM50K" in sale and "500.000₫" in sale, sale
            event = ask(agent, "Shop có sự kiện nào sắp tới không?", jwt)
            assert "chưa có thời gian hoặc điều kiện" in event, event
            politics = ask(agent, "Tình hình chính trị hiện nay thế nào?", jwt)
            assert "không trao đổi về chủ đề này" in politics, politics
            print("PASS: greeting, sale, event uncertainty and political-topic refusal")

        with httpx.Client(base_url=SHOP_URL, timeout=15, follow_redirects=True) as shop:
            shop.post("/login", data={"email": "binh@demo.vn",
                                       "password": "demo1234", "next": "/"})
            jwt = shop.get("/api/chat-token").json()["token"]
            result = ask(agent, "Đơn SO10293 của tôi", jwt)
            assert "Không tìm thấy đơn này trong tài khoản của bạn" in result, result
            print("PASS: other customer cannot read that order")


if __name__ == "__main__":
    main()
