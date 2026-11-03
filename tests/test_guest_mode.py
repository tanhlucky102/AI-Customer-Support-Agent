"""Guest mode: a level-0 shop without customer accounts lets visitors chat without logging in."""

import unittest
from unittest.mock import patch

from tests import support  # noqa: I001  (sets the test environment first)

import httpx

from agent_server import app as app_module
from agent_server import store
from agent_server.app import app
from agent_server.rate_limit import ChatLimiter
from tests.support import FakeLLM, sse_events

GUEST_A = {"X-ATI-Guest": "a" * 32}
GUEST_B = {"X-ATI-Guest": "b" * 32}


def make_shop(shop_id: str, **kwargs) -> None:
    store.create_shop(shop_id=shop_id, public_key=f"pk_{shop_id}", faq_write_key=f"sk_{shop_id}",
                      title=shop_id, greeting="Hi", primary_color="#000000",
                      shop_url="http://localhost:8002", allowed_origins=["http://localhost:8002"],
                      chat_token_secret=f"{shop_id}-secret", **kwargs)


class GuestModeTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        support.reset_database()
        make_shop("open_shop", guest_chat=True)    # level 0, guests allowed
        make_shop("closed_shop")                    # level 0, login required
        # Level 1 with the flag forced on: guests must still be refused (they would reach orders).
        make_shop("api_shop", guest_chat=True, commerce_config={"profile": "standard",
                                                                "base_url": "http://localhost:9"},
                  commerce_api_key="key")

    async def asyncSetUp(self):
        self.limiter = patch.object(app_module, "chat_limiter", ChatLimiter(10, 30, 500))
        self.limiter.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://localhost:8000")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.limiter.stop()

    async def chat(self, shop_id: str, headers: dict, llm=None, **body) -> httpx.Response:
        with patch.object(app_module, "llm", llm or app_module.llm):
            return await self.client.post("/v1/chat", headers=headers, json={
                "shop_key": f"pk_{shop_id}", "message": "Giờ mở cửa?", **body})

    async def test_only_an_opted_in_shop_without_commerce_api_accepts_guests(self):
        self.assertEqual((await self.chat("open_shop", GUEST_A)).status_code, 200)
        self.assertEqual((await self.chat("open_shop", {})).status_code, 401)
        self.assertEqual((await self.chat("open_shop", {"X-ATI-Guest": "not-hex"})).status_code, 401)
        self.assertEqual((await self.chat("closed_shop", GUEST_A)).status_code, 401)
        self.assertEqual((await self.chat("api_shop", GUEST_A)).status_code, 401)
        config = await self.client.get("/v1/widget-config", params={"shop_key": "pk_open_shop"})
        self.assertTrue(config.json()["guest_chat"])
        config = await self.client.get("/v1/widget-config", params={"shop_key": "pk_api_shop"})
        self.assertFalse(config.json()["guest_chat"])

    async def test_a_logged_in_customer_still_uses_the_token(self):
        headers = {**GUEST_A, "Authorization": "Bearer " + support.token(
            "U1", "open_shop-secret", shop_id="open_shop")}
        llm = FakeLLM("Chào bạn.")
        await self.chat("open_shop", headers, llm=llm)
        names = {tool["function"]["name"] for tool in llm.requests[0]["tools"]}
        self.assertIn("escalate_to_human", names)
        self.assertIn("is logged in", llm.requests[0]["messages"][0]["content"])

    async def test_guest_gets_documents_only_and_no_handover(self):
        llm = FakeLLM("Shop mở cửa 8:00-17:00.")
        await self.chat("open_shop", GUEST_A, llm=llm)
        names = {tool["function"]["name"] for tool in llm.requests[0]["tools"]}
        self.assertEqual(names, {"search_knowledge_base", "get_promotions"})
        prompt = llm.requests[0]["messages"][0]["content"]
        self.assertIn("not logged in", prompt)
        self.assertIn("cannot be handed over", prompt)

    async def test_a_guest_sees_only_their_own_conversations(self):
        response = await self.chat("open_shop", GUEST_A)
        conversation_id = sse_events(response.text)[-1][1]["conversation_id"]
        own = await self.client.get("/v1/conversations", params={"shop_key": "pk_open_shop"}, headers=GUEST_A)
        self.assertIn(conversation_id, [row["id"] for row in own.json()["conversations"]])
        other = await self.client.get("/v1/conversations", params={"shop_key": "pk_open_shop"}, headers=GUEST_B)
        self.assertNotIn(conversation_id, [row["id"] for row in other.json()["conversations"]])
        stolen = await self.client.get(f"/v1/conversations/{conversation_id}",
                                       params={"shop_key": "pk_open_shop"}, headers=GUEST_B)
        self.assertEqual(stolen.status_code, 404)
        follow_up = await self.chat("open_shop", GUEST_B, conversation_id=conversation_id)
        self.assertEqual(follow_up.status_code, 404)


if __name__ == "__main__":
    unittest.main()
