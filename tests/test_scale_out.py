"""Several server processes, a second model, real delivery dates and the shared shop writers."""

import threading
import time
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from tests import support  # noqa: I001  (sets the test environment first)

import httpx
import litellm

from agent_server import app as app_module
from agent_server import store
from agent_server.app import app
from agent_server.commerce import ORDER_FIELDS
from agent_server.rate_limit import DbChatLimiter
from agent_server.tools import _order_for_model, date_facts
from shared.llm import LLMError, LLMProvider
from tests.support import sse_events


def chunk(content=None, finish=None):
    delta = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


class Stream:
    def __init__(self, parts, fail_after=None):
        self.parts, self.fail_after = parts, fail_after

    def __aiter__(self):
        return self._run()

    async def _run(self):
        for index, part in enumerate(self.parts):
            if self.fail_after is not None and index == self.fail_after:
                raise TimeoutError("stalled")
            yield part


def bearer(customer_id: str) -> dict:
    return {"Authorization": "Bearer " + support.token(customer_id, store.DEMO_TOKEN_SECRET)}


class DeliveryDateTest(unittest.TestCase):
    TODAY = date(2026, 11, 4)

    def test_day_counts_come_from_the_dates_not_from_the_model(self):
        late = {"status": "shipping", "eta": "2026-11-01"}
        self.assertEqual(date_facts(late, self.TODAY), {"days_past_eta": 3})
        delivered = {"status": "delivered", "eta": "2026-10-28", "delivered_at": "2026-10-30"}
        self.assertEqual(date_facts(delivered, self.TODAY), {"days_since_delivery": 5})
        # A shop that sends no delivery date: the count from the estimate is marked as such.
        self.assertEqual(date_facts({"status": "delivered", "eta": "2026-10-28"}, self.TODAY),
                         {"days_since_eta": 7})
        self.assertEqual(date_facts({"status": "shipping", "eta": "2026-11-09"}, self.TODAY), {})

    def test_the_delivery_date_is_a_canonical_field_the_model_sees(self):
        self.assertIn("delivered_at", ORDER_FIELDS)
        order = {"id": "SO1", "status": "delivered", "eta": "2026-10-28", "delivered_at": "2026-10-30",
                 "customer_id": "C1", "address": "secret"}
        shown = _order_for_model(order)
        self.assertEqual(shown["delivered_at"], "2026-10-30")
        self.assertIn("days_since_delivery", shown)
        self.assertNotIn("address", shown)


class FailoverTest(unittest.IsolatedAsyncioTestCase):
    def provider(self, primary_key="k"):
        backup = LLMProvider(model="ollama_chat/qwen2.5:7b", api_base="http://localhost:11434",
                             api_key="none", fallback=False)
        return LLMProvider(api_key=primary_key, fallback=backup)

    async def collect(self, provider):
        return [event async for event in provider.stream([{"role": "user", "content": "hi"}])]

    async def test_the_second_model_answers_when_the_first_fails_before_any_text(self):
        seen = []

        async def fake(**kwargs):
            seen.append(kwargs)
            if kwargs["model"].startswith("openai/"):
                raise litellm.AuthenticationError("bad key", "openai", kwargs["model"])
            return Stream([chunk("Xin "), chunk("chào", "stop")])

        with patch("litellm.acompletion", side_effect=fake):
            events = await self.collect(self.provider())
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "token"), "Xin chào")
        self.assertEqual(events[-1]["result"].model, "ollama_chat/qwen2.5:7b")
        self.assertNotIn("extra_body", seen[-1])  # the deepseek-only parameter is not sent elsewhere
        self.assertEqual(seen[-1]["api_base"], "http://localhost:11434")

    async def test_no_failover_after_text_reached_the_customer(self):
        models = []

        async def fake(**kwargs):
            models.append(kwargs["model"])
            return Stream([chunk("Xin "), chunk("chào")], fail_after=1)

        with patch("litellm.acompletion", side_effect=fake), self.assertRaises(LLMError):
            await self.collect(self.provider())
        self.assertEqual(len(models), 1)  # a second answer would repeat or contradict the first

    async def test_a_backup_alone_still_runs_the_agent(self):
        provider = self.provider(primary_key="")
        self.assertTrue(provider.enabled)

        async def fake(**kwargs):
            return Stream([chunk("OK", "stop")])

        with patch("litellm.acompletion", side_effect=fake):
            events = await self.collect(provider)
        self.assertEqual(events[-1]["result"].model, "ollama_chat/qwen2.5:7b")

    def test_without_settings_there_is_no_backup(self):
        self.assertIsNone(LLMProvider(api_key="k").fallback)  # tests set LLM_FALLBACK_MODEL=""


class SharedStateTest(unittest.IsolatedAsyncioTestCase):
    """What turns share lives in PostgreSQL, so two server processes see the same locks and limits."""

    @classmethod
    def setUpClass(cls):
        support.reset_database()

    def test_conversation_claim_is_exclusive_and_lapses(self):
        store.create_conversation("c-claim", "demo_shop", "C017", "hi")
        self.assertTrue(store.claim_conversation("c-claim", 60))
        self.assertFalse(store.claim_conversation("c-claim", 60))  # e.g. another process
        store.release_conversation("c-claim")
        self.assertTrue(store.claim_conversation("c-claim", 0.05))
        time.sleep(0.1)
        self.assertTrue(store.claim_conversation("c-claim", 60))  # a dead holder's claim ran out
        store.release_conversation("c-claim")

    def test_advisory_lock_lets_one_holder_in_at_a_time(self):
        spans = []

        def hold():
            with store.advisory_lock("faq:test"):
                entered = time.monotonic()
                time.sleep(0.2)
                spans.append((entered, time.monotonic()))

        threads = [threading.Thread(target=hold) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        (first_in, first_out), (second_in, _) = sorted(spans)
        self.assertGreaterEqual(second_in, first_out)

    def test_database_limiter_counts_across_instances(self):
        one, two = DbChatLimiter(2, 100, 0), DbChatLimiter(2, 100, 0)  # two server processes
        self.assertIsNone(one.check("demo_shop", "C900", "10.0.0.1"))
        self.assertIsNone(two.check("demo_shop", "C900", "10.0.0.2"))
        reason, retry_after = one.check("demo_shop", "C900", "10.0.0.3")
        self.assertEqual(reason, "customer_rate_limited")
        self.assertTrue(1 <= retry_after <= 60)
        # Guests choose their own ID, so they are counted by IP.
        daily = DbChatLimiter(0, 0, 0, daily_per_customer=1)
        self.assertIsNone(daily.check("demo_shop", "guest:a", "10.0.0.9", guest=True))
        self.assertEqual(daily.check("demo_shop", "guest:b", "10.0.0.9", guest=True)[0], "customer_daily_limit")

    async def test_a_turn_waits_for_the_turn_another_process_is_running(self):
        store.create_conversation("c-busy", "demo_shop", "C017", "hi")
        self.assertTrue(store.claim_conversation("c-busy", 60))  # held elsewhere
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000")
        try:
            with patch.object(app_module, "TURN_TIMEOUT", 0.3):
                response = await client.post("/v1/chat", headers=bearer("C017"), json={
                    "shop_key": "pk_demo_shop", "message": "Phí giao hàng?", "conversation_id": "c-busy"})
            self.assertEqual(sse_events(response.text)[-1][0], "error")
            store.release_conversation("c-busy")
            response = await client.post("/v1/chat", headers=bearer("C017"), json={
                "shop_key": "pk_demo_shop", "message": "Phí giao hàng?", "conversation_id": "c-busy"})
            self.assertEqual(sse_events(response.text)[-1][0], "done")
            self.assertTrue(store.claim_conversation("c-busy", 1))  # released after the turn
        finally:
            await client.aclose()

    async def test_only_the_widget_files_are_served(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://localhost:8000") as client:
            for name, kind in (("loader.js", "javascript"), ("frame.css", "css"), ("frame-core.js", "javascript")):
                response = await client.get(f"/widget/{name}")
                self.assertEqual(response.status_code, 200, name)
                self.assertIn(kind, response.headers["content-type"])
            for name in ("frame.html", "..%2Fagent_server%2Fapp.py", "missing.js"):
                self.assertEqual((await client.get(f"/widget/{name}")).status_code, 404, name)


class ShopWriterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.reset_database()

    def test_one_writer_for_create_update_and_refresh(self):
        store.create_shop(shop_id="w_shop", public_key="pk_w", faq_write_key="sk_w", title="W", greeting="Hi",
                          primary_color="#000000", shop_url="https://w.example/", chat_token_secret="s",
                          allowed_origins=["https://w.example/path"], commerce_config={"base_url": "https://w.example"})
        shop = store.shop_by_id("w_shop")
        self.assertEqual((shop["shop_url"], shop["allowed_origins"]), ("https://w.example", ["https://w.example"]))
        self.assertIsNotNone(store.shop_for_write_key("w_shop", "sk_w"))
        self.assertIsNone(store.shop_for_write_key("w_shop", "wrong"))
        self.assertTrue(store.set_chat_token_secret("w_shop", "s2"))
        self.assertFalse(store.set_chat_token_secret("nobody", "s2"))
        store.refresh_shop("w_shop", faq_write_key="sk_new", chat_token_secret="s3", shop_url="https://w.example",
                           allowed_origins=["https://w.example"], commerce_config=None, guest_chat=True)
        shop = store.shop_by_id("w_shop")
        self.assertIsNone(shop["commerce_config"])  # None clears it: the shop no longer has an API
        self.assertTrue(store.guest_allowed(shop))
        self.assertTrue(store.verify_faq_key("w_shop", "sk_new"))


if __name__ == "__main__":
    unittest.main()
