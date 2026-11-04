"""Regression tests for the defects found by the four-area review of 04/11/2026."""

import logging
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from tests import support  # noqa: I001  (sets the test environment first)

import httpx

from agent_server import agent, store
from agent_server import app as app_module
from agent_server.app import app
from agent_server.commerce import RestCommerceAdapter, config_problems
from agent_server.rate_limit import ChatLimiter
from agent_server.tools import OrderArgs, TurnContext
from shared.pii import PiiFilter, mask_address, mask_email, mask_phone
from shared.text import looks_english
from tests.support import FakeLLM
from tests.test_resilience import Stream


def adapter(operations: dict, api, **extra) -> RestCommerceAdapter:
    return RestCommerceAdapter({"base_url": "http://api", "operations": operations, **extra}, "k",
                               "http://shop", transport=httpx.MockTransport(api))


def context(shop: dict | None = None, customer: str = "C1", turn_key: str = "t") -> TurnContext:
    return TurnContext(shop=shop or {"shop_id": "s", "shop_url": "http://shop", "title": "Shop"},
                       customer_id=customer, conversation_id="c", turn_key=turn_key, adapter=None,
                       search=lambda *args, **kw: [])


class CommerceReviewTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_customer_filter_inside_a_param_template_is_filled_in(self):
        seen = []

        def api(request):
            seen.append(dict(request.url.params))
            return httpx.Response(200, json=[{"id": "A1", "status": "pending", "owner": "K3"}])

        shop = adapter({"list_orders": {"path": "/orders", "params": {"filter": "customer:{customer_id}"}}}, api)
        await shop.list_orders("K3", 5, None)
        self.assertEqual(seen[0]["filter"], "customer:K3")
        self.assertIn("unknown variable", " ".join(config_problems(
            {"base_url": "http://api", "operations": {"get_order": {"path": "/o/{order}/{customer_id}"}}})))

    async def test_one_malformed_record_does_not_hide_the_other_orders(self):
        shop = adapter({"list_orders": {"path": "/c/{customer_id}/orders"}}, lambda r: httpx.Response(
            200, json=[{"id": None, "status": "pending"}, {"id": "A2", "status": "delivered"}]))
        self.assertEqual([order["id"] for order in await shop.list_orders("K1", 5, None)], ["A2"])

    async def test_a_loosely_matching_shop_cannot_show_another_order(self):
        shop = adapter({"get_order": {"path": "/c/{customer_id}/o/{order_id}"}},
                       lambda r: httpx.Response(200, json={"id": "SO10293-B", "status": "pending"}))
        self.assertIsNone(await shop.get_order("SO10293", "K1"))
        same = adapter({"get_order": {"path": "/c/{customer_id}/o/{order_id}"}},
                       lambda r: httpx.Response(200, json={"id": "#so10293", "status": "pending"}))
        self.assertEqual((await same.get_order("SO10293", "K1"))["id"], "#so10293")

    def test_order_ids_as_numbers_or_with_vietnamese_letters_are_accepted(self):
        self.assertEqual(OrderArgs.model_validate({"order_id": 10293}).order_id, "10293")
        self.assertEqual(OrderArgs.model_validate({"order_id": "ĐH10293"}).order_id, "ĐH10293")


class AgentReviewTest(unittest.IsolatedAsyncioTestCase):
    async def run_agent(self, llm, ctx, message="câu hỏi"):
        return [event async for event in agent.run(ctx, [], message, llm=llm)]

    async def test_grouped_citations_are_understood(self):
        self.assertEqual(agent.citation_numbers("A [1, 2]. B [3-5]. C [7]"), {1, 2, 3, 4, 5, 7})

    async def test_the_same_section_found_twice_keeps_one_number(self):
        ctx = context()
        self.assertEqual(ctx.cite("Đổi trả – Thời hạn", "u"), ctx.cite("Đổi trả – Thời hạn", "u"))

    async def test_a_guest_is_never_offered_a_handover(self):
        events = await self.run_agent(FakeLLM(""), context(customer="guest:" + "a" * 32))
        self.assertNotIn("chuyển cho nhân viên", reply_text_from(events))

    async def test_handover_uses_the_shops_own_promise_and_the_customers_language(self):
        shop = {"shop_id": "demo_shop", "shop_url": "http://localhost:8001", "title": "Demo",
                "handover_note": "Nhân viên phản hồi trong 2 giờ.", "handover_note_en": "Staff reply within 2 hours."}
        support.reset_database()
        store.create_conversation("c", "demo_shop", "C1", "x")
        llm = FakeLLM([("escalate_to_human", {"reason": "customer_request", "transcript_summary": "wants a person"})])
        english = reply_text_from(await self.run_agent(llm, context(shop), "I want to talk to a real person please"))
        self.assertIn("Staff reply within 2 hours.", english)
        default = {**shop, "handover_note": "", "handover_note_en": ""}
        llm = FakeLLM([("escalate_to_human", {"reason": "customer_request", "transcript_summary": "gặp nhân viên"})])
        vietnamese = reply_text_from(await self.run_agent(llm, context(default, turn_key="t2"), "cho gặp nhân viên"))
        self.assertNotIn("24 giờ", vietnamese)

    async def test_parallel_tool_calls_sharing_an_index_stay_separate(self):
        def call(index, call_id, name, arguments):
            function = SimpleNamespace(name=name, arguments=arguments)
            return SimpleNamespace(index=index, id=call_id, function=function)

        def chunk(calls):
            delta = SimpleNamespace(content=None, tool_calls=calls)
            return SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=delta, finish_reason=None)])

        stream = Stream([chunk([call(0, "a", "search_knowledge_base", '{"query": "x"}')]),
                         chunk([call(0, "b", "get_order_status", '{"order_id": "SO1"}')])])

        async def fake(**kwargs):
            return stream

        from shared.llm import LLMProvider
        with patch("litellm.acompletion", side_effect=fake):
            events = [e async for e in LLMProvider(api_key="k").stream([{"role": "user", "content": "x"}])]
        calls = events[-1]["result"].tool_calls
        self.assertEqual([c["name"] for c in calls], ["search_knowledge_base", "get_order_status"])


def reply_text_from(events: list[dict]) -> str:
    return next(event["reply"] for event in events if event["type"] == "done")


class TextAndPiiReviewTest(unittest.TestCase):
    def test_english_with_place_names_and_short_questions(self):
        for text in ("Do you deliver to Ho Chi Minh City?", "Do you ship to Nha Trang?", "refund?",
                     "iPhone 15 price?"):
            self.assertTrue(looks_english(text), text)
        for text in ("Hello", "Minh oi don hang dau", "co ship khong", "gia iphone 16 pro max"):
            self.assertFalse(looks_english(text), text)

    def test_masking_handles_numbers_landlines_and_vietnamese_mail_boxes(self):
        self.assertEqual(mask_phone(912345678), "***678")
        self.assertNotIn("3823", mask_phone("024 3823 4567"))
        self.assertNotIn("345", mask_phone("(+84) 912 345 678")[:-3])
        self.assertEqual(mask_address({"street": "12 Lê Lợi"}), "***")
        self.assertEqual(mask_email("nguyễn.van@gmail.com"), "n***@gmail.com")

    def test_tracebacks_in_logs_are_masked(self):
        record = None
        try:
            raise ValueError("customer phone 0912345678")
        except ValueError:
            import sys
            record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
        PiiFilter().filter(record)
        self.assertNotIn("0912345678", record.exc_text)


class ServerReviewTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        support.reset_database()

    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000")
        self.limiter = patch.object(app_module, "chat_limiter", ChatLimiter(100, 100, 1000))
        self.limiter.start()

    async def asyncTearDown(self):
        await self.client.aclose()
        self.limiter.stop()

    def bearer(self, customer: str, **kwargs) -> dict:
        return {"Authorization": "Bearer " + support.token(customer, store.DEMO_TOKEN_SECRET, **kwargs)}

    async def test_oversized_bodies_and_nul_bytes_are_refused_cleanly(self):
        big = await self.client.post("/v1/chat", content=b"x" * 100_000, headers={"Content-Type": "application/json"})
        self.assertEqual(big.status_code, 413)
        nul = await self.client.get("/v1/widget-config", params={"shop_key": "pk\x00x"})
        self.assertEqual(nul.status_code, 400)
        bad = await self.client.post("/v1/chat", json={"shop_key": "pk_demo_shop", "message": "x" * 600},
                                     headers=self.bearer("C1"))
        self.assertEqual(bad.status_code, 422)
        self.assertNotIn("xxxx", bad.text)  # the input is not echoed back

    async def test_feedback_is_stored_once_per_conversation_and_counted_per_shop(self):
        store.create_conversation("fb-1", "demo_shop", "C7", "x")
        for rating in ("yes", "yes", "no"):
            response = await self.client.post("/v1/feedback", headers=self.bearer("C7"), json={
                "shop_key": "pk_demo_shop", "conversation_id": "fb-1", "rating": rating})
            self.assertEqual(response.status_code, 200)
        counts = await self.client.get("/v1/shops/demo_shop/feedback",
                                       headers={"Authorization": f"Bearer {store.DEMO_FAQ_WRITE_KEY}"})
        self.assertEqual((counts.json()["yes"], counts.json()["no"]), (0, 1))
        self.assertNotIn("feedback", (await self.client.get("/v1/demo-stats")).json())

    async def test_empty_conversations_are_hidden_and_tickets_survive_cleanup(self):
        store.create_conversation("empty-1", "demo_shop", "C8", "câu hỏi bị cắt")
        listed = await self.client.get("/v1/conversations", params={"shop_key": "pk_demo_shop"},
                                       headers=self.bearer("C8"))
        self.assertEqual(listed.json()["conversations"], [])
        store.create_conversation("ticket-1", "demo_shop", "C8", "x")
        store.create_ticket(conversation_id="ticket-1", shop_id="demo_shop", customer_id="C8",
                            reason="complaint", summary="giao trễ", idempotency_key="k-1")
        store.delete_empty_conversation("ticket-1")
        self.assertTrue(store.list_tickets("demo_shop"))

    async def test_tickets_page_and_close(self):
        store.create_conversation("ticket-2", "demo_shop", "C9", "x")
        code = store.create_ticket(conversation_id="ticket-2", shop_id="demo_shop", customer_id="C9",
                                   reason="complaint", summary="đổi máy", idempotency_key="k-2")
        ticket_id = int(code.removeprefix("TK"))
        key = {"Authorization": f"Bearer {store.DEMO_FAQ_WRITE_KEY}"}
        closed = await self.client.post(f"/v1/shops/demo_shop/tickets/{ticket_id}/close", headers=key)
        self.assertEqual(closed.status_code, 200)
        open_only = await self.client.get("/v1/shops/demo_shop/tickets", params={"status": "open"}, headers=key)
        self.assertNotIn(ticket_id, [row["id"] for row in open_only.json()["tickets"]])

    async def test_widget_logout_accepts_an_expired_token_and_cuts_off_older_ones(self):
        issued = int(time.time()) - 1000
        expired = self.bearer("C10", issued_at=issued, expires_in=900)  # expired 100 s ago
        older = self.bearer("C10", issued_at=issued - 60, expires_in=1500)  # still valid
        self.assertEqual((await self.client.get("/v1/conversations", params={"shop_key": "pk_demo_shop"},
                                                headers=older)).status_code, 200)
        logout = await self.client.post("/v1/logout", json={"shop_key": "pk_demo_shop"}, headers=expired)
        self.assertEqual(logout.status_code, 200)
        self.assertEqual((await self.client.get("/v1/conversations", params={"shop_key": "pk_demo_shop"},
                                                headers=older)).status_code, 401)

    async def test_a_token_cannot_claim_a_guest_identity(self):
        response = await self.client.get("/v1/conversations", params={"shop_key": "pk_demo_shop"},
                                         headers=self.bearer("guest:" + "a" * 32))
        self.assertEqual(response.status_code, 401)


class LimitsReviewTest(unittest.TestCase):
    def test_one_sender_cannot_use_up_the_shops_day_and_guests_count_by_ip(self):
        limiter = ChatLimiter(0, 0, 100, daily_per_customer=2, daily_per_ip=3)
        self.assertIsNone(limiter.check("s", "C1", "1.1.1.1"))
        self.assertIsNone(limiter.check("s", "C1", "1.1.1.1"))
        self.assertEqual(limiter.check("s", "C1", "1.1.1.1")[0], "customer_daily_limit")
        self.assertIsNone(limiter.check("s", "guest:" + "a" * 32, "2.2.2.2", guest=True))
        self.assertIsNone(limiter.check("s", "guest:" + "b" * 32, "2.2.2.2", guest=True))
        self.assertEqual(limiter.check("s", "guest:" + "c" * 32, "2.2.2.2", guest=True)[0],
                         "customer_daily_limit")  # a new guest ID on the same IP is the same sender

    def test_the_shops_daily_count_survives_a_restart(self):
        limiter = ChatLimiter(0, 0, 3, daily_used=lambda shop_id: 3)
        self.assertEqual(limiter.check("s", "C1", "1.1.1.1")[0], "daily_quota_exceeded")

    def test_origins_are_compared_as_browsers_write_them(self):
        self.assertEqual(store.origin_of("https://Shop.VN:443/x"), "https://shop.vn")
        self.assertEqual(store.origin_of("http://localhost:8001"), "http://localhost:8001")


class RetrievalReviewTest(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_refuses_a_wrong_or_empty_folder(self):
        from rag.ingest import ingest_dir
        with self.assertRaises(FileNotFoundError):
            await ingest_dir("demo_shop", "http://shop", Path("data/kb/does-not-exist"))
        with TemporaryDirectory() as empty, self.assertRaises(ValueError):
            await ingest_dir("demo_shop", "http://shop", Path(empty))

    async def test_faq_keywords_are_searchable_and_link_to_the_shop(self):
        from rag.ingest import index_faqs
        from rag.search import search
        support.reset_database()
        store.create_shop(shop_id="kw_shop", public_key="pk_kw", faq_write_key="sk_kw", title="KW",
                          greeting="Hi", primary_color="#000000", shop_url="http://kw.example",
                          allowed_origins=["http://kw.example"], chat_token_secret="s")
        faq = {"id": "hoa-don", "question": "Shop có xuất hoá đơn không?",
               "answer": "Có, gửi yêu cầu qua email.", "keywords": ["VAT"]}
        await index_faqs("kw_shop", "http://kw.example", [faq])
        hits = search("VAT", "kw_shop", apply_threshold=False)
        self.assertEqual(hits[0]["doc"], "faq-hoa-don")
        self.assertEqual(hits[0]["url"], "http://kw.example")

    def test_markdown_files_cannot_take_an_faq_slug(self):
        from rag.documents import load_dir
        with TemporaryDirectory() as folder:
            Path(folder, "faq-x.md").write_text("---\ntitle: X\n---\n## A\nB", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_dir(Path(folder))


if __name__ == "__main__":
    unittest.main()
