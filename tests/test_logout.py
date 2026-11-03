"""Logging out cuts off the chat tokens already issued to the customer (contract C3)."""

import json
import sys
import time
import unittest
from unittest.mock import patch

from tests import support  # noqa: I001  (sets the test environment first)

import httpx

from agent_server import store
from agent_server.app import app
from agent_server.auth import verify_customer_token

sys.path.append(str(support.ROOT / "sdk"))
from ati_sdk import revoke_user_tokens  # noqa: E402


def bearer(customer_id: str, issued_at: int) -> dict:
    return {"Authorization": "Bearer " + support.token(customer_id, store.DEMO_TOKEN_SECRET,
                                                        issued_at=issued_at)}


class LogoutTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        support.reset_database()

    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://localhost:8000")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def history(self, headers: dict) -> int:
        response = await self.client.get("/v1/conversations", params={"shop_key": "pk_demo_shop"},
                                         headers=headers)
        return response.status_code

    async def test_widget_logout_cuts_off_the_token_but_not_a_new_login(self):
        before = int(time.time()) - 10
        old = bearer("C0101", before)
        self.assertEqual(await self.history(old), 200)
        logout = await self.client.post("/v1/logout", json={"shop_key": "pk_demo_shop"}, headers=old)
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(await self.history(old), 401)                                  # stolen copy useless
        self.assertEqual(await self.history(bearer("C0101", int(time.time()) + 2)), 200)  # logs in again
        self.assertEqual(await self.history(bearer("C0102", before)), 200)              # others unaffected
        anonymous = await self.client.post("/v1/logout", json={"shop_key": "pk_demo_shop"})
        self.assertEqual(anonymous.status_code, 401)

    async def test_shop_backend_logout_needs_the_write_key_and_covers_every_device(self):
        laptop = bearer("C0201", int(time.time()) - 30)
        phone = bearer("C0201", int(time.time()) - 5)
        url, body = "/v1/shops/demo_shop/logout", {"customer_id": "C0201", "logout_at": int(time.time())}
        wrong = await self.client.post(url, json=body, headers={"Authorization": "Bearer wrong"})
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(await self.history(laptop), 200)
        right = await self.client.post(url, json=body,
                                       headers={"Authorization": f"Bearer {store.DEMO_FAQ_WRITE_KEY}"})
        self.assertEqual(right.status_code, 200)
        self.assertEqual(await self.history(laptop), 401)
        self.assertEqual(await self.history(phone), 401)

    def test_a_token_living_longer_than_an_hour_is_refused(self):
        shop = {"shop_id": "demo_shop", "chat_token_secret": "s"}
        self.assertEqual(verify_customer_token(support.token("42", "s", expires_in=900), shop), "42")
        self.assertIsNone(verify_customer_token(support.token("42", "s", expires_in=7200), shop))

    def test_sdk_logout_call_reaches_the_agent_server_and_never_raises(self):
        sent = []

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(request, timeout):
            sent.append(request)
            return Response()

        with patch("urllib.request.urlopen", urlopen):
            self.assertTrue(revoke_user_tokens("K1", shop_id="second_shop", write_key="sk",
                                               server_url="http://agent/"))
        self.assertEqual(sent[0].full_url, "http://agent/v1/shops/second_shop/logout")
        self.assertEqual(sent[0].get_header("Authorization"), "Bearer sk")
        self.assertEqual(json.loads(sent[0].data)["customer_id"], "K1")
        with patch("urllib.request.urlopen", side_effect=OSError("agent server down")):
            self.assertFalse(revoke_user_tokens("K1", shop_id="second_shop", write_key="sk",
                                                server_url="http://agent"))


if __name__ == "__main__":
    unittest.main()
