"""Regression tests for the defects found in the final code review."""

import json
import sys
import unittest
from unittest.mock import patch

from tests import support  # noqa: I001  (sets the test environment first)

import httpx

from agent_server import app as app_module
from agent_server import store
from agent_server.app import app
from agent_server.commerce import CommerceError, RestCommerceAdapter, config_problems
from agent_server.rate_limit import ChatLimiter
from rag.chunking import split_section
from rag.documents import Document, Section, parse
from shared.text import query_terms
from tests.support import FakeLLM, sse_events

sys.path.append(str(support.ROOT / "sdk"))
from ati_sdk import sign_user_token  # noqa: E402

ORDERS = {"A1": {"code": "A1", "state": "SHIPPED", "buyer": {"id": "U1"}},
          "B2": {"code": "B2", "state": "SHIPPED", "buyer": {"id": "U2"}}}


def admin_api(request: httpx.Request) -> httpx.Response:
    """An admin-style API that returns any customer's order: ownership must be checked by us."""
    if request.url.path.startswith("/orders/"):
        order = ORDERS.get(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json={"order": order}) if order else httpx.Response(404)
    if request.url.path == "/orders":
        return httpx.Response(200, json={"orders": list(ORDERS.values())})
    return httpx.Response(200, json={"oops": True})


def adapter(config: dict) -> RestCommerceAdapter:
    return RestCommerceAdapter({"base_url": "http://shop", **config}, "k", "http://shop",
                               transport=httpx.MockTransport(admin_api))


class CommerceScopingTest(unittest.IsolatedAsyncioTestCase):
    async def test_unscoped_customer_operation_is_refused_and_not_offered(self):
        config = {"operations": {"get_order": {"path": "/orders/{order_id}", "result": "order"}}}
        self.assertTrue(config_problems({"base_url": "http://shop", **config}))
        unsafe = adapter(config)
        self.assertNotIn("get_order", unsafe.capabilities)
        with self.assertRaises(CommerceError):
            await unsafe.get_order("B2", "U1")

    async def test_owner_field_hides_other_customers_records(self):
        scoped = adapter({"operations": {
            "get_order": {"path": "/orders/{order_id}", "result": "order", "owner_field": "buyer.id",
                          "fields": {"id": "code", "status": "state"}},
            "list_orders": {"path": "/orders", "result": "orders"}},
            "status_map": {"SHIPPED": "shipping"}})
        self.assertIsNotNone(await scoped.get_order("A1", "U1"))
        self.assertIsNone(await scoped.get_order("B2", "U1"))
        self.assertEqual([order["id"] for order in await scoped.list_orders("U1", 5, None)], ["A1"])

    async def test_absolute_path_is_rejected(self):
        config = {"operations": {"get_customer": {"path": "http://169.254.169.254/{customer_id}"}}}
        self.assertTrue(config_problems({"base_url": "http://shop", **config}))
        self.assertNotIn("get_customer", adapter(config).capabilities)

    async def test_ok_response_without_the_expected_record_is_an_error(self):
        broken = adapter({"operations": {"get_customer": {"path": "/c/{customer_id}", "result": "data.user"}}})
        with self.assertRaises(CommerceError):
            await broken.get_customer("U1")

    async def test_status_with_several_shop_values_is_filtered_locally(self):
        seen = []

        def api(request):
            seen.append(dict(request.url.params))
            return httpx.Response(200, json=[{"id": "1", "state": "OUT"}, {"id": "2", "state": "SHIPPED"},
                                             {"id": "3", "state": "DONE"}])

        shop = RestCommerceAdapter({"base_url": "http://shop", "operations": {"list_orders": {
            "path": "/u/{customer_id}/orders", "params": {"limit": "{limit}", "state": "{status}"},
            "fields": {"status": "state"}}},
            "status_map": {"SHIPPED": "shipping", "OUT": "shipping", "DONE": "delivered"}},
            "k", "http://shop", transport=httpx.MockTransport(api))
        orders = await shop.list_orders("U1", 5, "shipping")
        self.assertEqual([order["id"] for order in orders], ["1", "2"])
        self.assertEqual(seen[0], {"limit": "50"})  # No single shop status to send: fetch, then filter.

    async def test_order_list_link_is_the_list_page_or_names_the_order_it_opens(self):
        from agent_server.tools import ListOrdersArgs, TurnContext, list_customer_orders
        api = httpx.MockTransport(lambda r: httpx.Response(200, json=[{"id": "A1", "status": "pending"}]))
        operations = {"list_orders": {"path": "/c/{customer_id}/orders"}}
        for links, expected in (({"order": "/o/{order_id}", "orders": "/my-orders"}, ("Đơn hàng của tôi", "http://shop/my-orders")),
                                ({"order": "/o/{order_id}"}, ("Xem đơn A1", "http://shop/o/A1"))):
            adapter = RestCommerceAdapter({"base_url": "http://api", "links": links, "operations": operations},
                                          "k", "http://shop", transport=api)
            ctx = TurnContext(shop={"shop_id": "other", "shop_url": "http://shop"}, customer_id="U1",
                              conversation_id="c", turn_key="t", adapter=adapter, search=lambda *a, **k: [])
            await list_customer_orders(ctx, ListOrdersArgs())
            self.assertEqual((ctx.links[0]["label"], ctx.links[0]["href"]), expected)

    async def test_an_oversized_shop_response_is_cut_off_while_streaming(self):
        from agent_server.commerce import MAX_RESPONSE_BYTES, CommerceError
        sent = []

        async def chunks():
            for _ in range(10):  # 10 x 200 KB: reading must stop after about 1 MB.
                sent.append(1)
                yield b" " * 200_000

        api = httpx.MockTransport(lambda r: httpx.Response(200, content=chunks()))
        shop = RestCommerceAdapter({"base_url": "http://api", "operations": {
            "get_order": {"path": "/c/{customer_id}/o/{order_id}"}}}, "k", "http://shop", transport=api)
        with self.assertRaises(CommerceError):
            await shop.get_order("A1", "U1")
        self.assertLessEqual(len(sent) * 200_000, MAX_RESPONSE_BYTES + 200_000)

    async def test_broken_link_template_does_not_break_the_order(self):
        shop = RestCommerceAdapter({"base_url": "http://shop", "links": {"order": "/o/{code}"}, "operations": {
            "get_order": {"path": "/c/{customer_id}/o/{order_id}"}}}, "k", "http://shop",
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "X1", "status": "pending"})))
        self.assertIsNone((await shop.get_order("X1", "U1"))["url"])


def bearer(customer_id: str) -> dict:
    return {"Authorization": "Bearer " + support.token(customer_id, store.DEMO_TOKEN_SECRET)}


class ChatRobustnessTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        support.reset_database()
        support.ingest_kb("demo_shop", store.DEMO_SHOP_URL)

    async def asyncSetUp(self):
        self.limiter = patch.object(app_module, "chat_limiter", ChatLimiter(100, 100, 1000))
        self.limiter.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.limiter.stop()

    async def chat(self, message: str, llm=None, headers=None):
        with patch.object(app_module, "llm", llm or app_module.llm):
            return await self.client.post("/v1/chat", json={"shop_key": "pk_demo_shop", "message": message},
                                          headers=headers or bearer("C7"))

    async def test_malformed_token_header_is_401_not_500(self):
        for token in ("W10.e30.AA", "Ig.e30.AA", "e30.W10.AA"):  # list / string / list claims
            response = await self.client.post("/v1/chat", json={"shop_key": "pk_demo_shop", "message": "hi"},
                                              headers={"Authorization": "Bearer " + token})
            self.assertEqual(response.status_code, 401, token)

    async def test_control_characters_are_dropped_from_messages(self):
        response = await self.chat("phí\u0000 vận chuyển dưới 500.000₫")
        self.assertEqual(sse_events(response.text)[-1][0], "done")

    async def test_a_failing_tool_does_not_end_the_turn(self):
        llm = FakeLLM([("search_knowledge_base", {"query": "đổi trả"})], "Xin lỗi, mình chưa tra được.")
        with patch.object(app_module, "search", side_effect=RuntimeError("pool timeout")):
            response = await self.chat("đổi trả", llm=llm)
        self.assertEqual(sse_events(response.text)[-1][0], "done")
        self.assertEqual(json.loads(llm.requests[1]["messages"][-1]["content"])["error"], "tool_failed")

    async def test_text_written_before_a_tool_call_is_taken_back(self):
        llm = FakeLLM(("I'll look that up.", [("search_knowledge_base", {"query": "đổi trả"})]),
                      "Bạn được đổi trong 30 ngày.")
        response = await self.chat("đổi trả thế nào", llm=llm)
        events = sse_events(response.text)
        self.assertIn("replace", [name for name, _ in events])
        self.assertEqual(support.reply_text(response.text), "Bạn được đổi trong 30 ngày.")
        self.assertIsNone(llm.requests[1]["messages"][-2]["content"])  # Not echoed to the model.

    async def test_an_english_question_asks_for_an_english_answer(self):
        llm = FakeLLM("Phones can be returned within 30 days.")
        await self.chat("What is your return policy for phones?", llm=llm)
        self.assertIn("in English", llm.requests[0]["messages"][1]["content"])
        llm = FakeLLM("Được bạn nhé.")
        await self.chat("dat may len de sac khong day ma no ko vo pin", llm=llm)
        self.assertNotIn("English", llm.requests[0]["messages"][1]["content"])

    async def test_traces_keep_no_personal_data(self):
        llm = FakeLLM([("escalate_to_human", {"reason": "complaint",
                                              "transcript_summary": "Khách 0912345678, an@demo.vn phàn nàn"})])
        await self.chat("tôi muốn khiếu nại", llm=llm)
        from shared.db import connect
        with connect() as db:
            row = db.execute("SELECT tools_called FROM traces ORDER BY id DESC LIMIT 1").fetchone()
        stored = json.dumps(row["tools_called"], ensure_ascii=False)
        self.assertNotIn("0912345678", stored)
        self.assertNotIn("an@demo.vn", stored)
        self.assertIn("escalate_to_human", stored)

    async def test_a_failed_first_turn_leaves_no_empty_conversation(self):
        with patch.object(app_module, "append_turn", side_effect=RuntimeError("db down")):
            response = await self.chat("phí giao hàng", headers=bearer("C8"))
        self.assertEqual(sse_events(response.text)[-1][0], "error")
        listing = await self.client.get("/v1/conversations", params={"shop_key": "pk_demo_shop"},
                                        headers=bearer("C8"))
        self.assertEqual(listing.json()["conversations"], [])

    async def test_faq_push_reports_whether_it_was_indexed(self):
        store.create_shop(shop_id="faq_shop", public_key="pk_faq", faq_write_key="sk_faq", title="F",
                          greeting="Hi", primary_color="#000000", shop_url="http://localhost:8005",
                          allowed_origins=["http://localhost:8005"], chat_token_secret="s")
        faq = {"id": "x", "question": "Giờ mở cửa?", "answer": "8:00-17:00", "keywords": []}
        response = await self.client.put("/v1/shops/faq_shop/faqs", json={"faqs": [faq]},
                                         headers={"Authorization": "Bearer sk_faq"})
        self.assertTrue(response.json()["indexed"])


class TextAndChunkingTest(unittest.TestCase):
    def test_accented_content_words_are_not_stopwords(self):
        self.assertIn("muon", query_terms("Giao hàng muộn thì sao?"))   # muộn, not muốn
        self.assertIn("cua", query_terms("Cửa hàng ở đâu?"))            # cửa, not của
        self.assertNotIn("khong", query_terms("có được đổi trả không"))
        self.assertNotIn("toan_bang", query_terms("thanh toán với bằng"))  # no bigram across "với"

    def test_english_is_told_apart_from_unaccented_vietnamese(self):
        from shared.text import looks_english
        for text in ("Do I have to pay shipping on a 250k order?", "Who won the 2022 FIFA World Cup?",
                     "how do i back up my iphone to my laptop",
                     # Short questions with few function words (found by scripts/bad_cases.py).
                     "Is the iPhone 16 in stock?", "Hi, do you ship to Da Nang?", "Hello, any discount today?"):
            self.assertTrue(looks_english(text), text)
        for text in ("mua tai nghe tra gop duoc ko, the atm thi sao", "don 450k co mat phi ship ko",
                     "upate ios xong bi dung o logo tao tao hon 1 tieng roi", "Đơn của tôi đâu?", "ok",
                     "hi shop", "gia iphone 16 pro max", "check don hang giup em"):
            self.assertFalse(looks_english(text), text)

    def test_faq_citation_label_does_not_repeat_the_question(self):
        from shared.text import source_label
        self.assertEqual(source_label({"doc_title": "Giờ mở cửa?", "section": "Giờ mở cửa?"}), "Giờ mở cửa?")
        self.assertEqual(source_label({"doc_title": "Đổi trả", "section": "Thời hạn"}), "Đổi trả – Thời hạn")

    def test_no_chunk_repeats_a_subset_of_the_previous_one(self):
        text = "\n".join(["a " * 20, "b " * 20, "c " * 20, "d " * 70])
        chunks = split_section(Section(0, "H", text), max_tokens=100, overlap=70)
        for previous, current in zip(chunks, chunks[1:]):
            self.assertFalse(set(current.split("\n")) <= set(previous.split("\n")), chunks)

    def test_document_intro_is_indexed_with_the_first_section(self):
        doc = parse("---\ntitle: T\ncategory: payment\n---\n# T\nChỉ nhận COD và MoMo.\n\n## A\nNội dung.\n", "t")
        self.assertIn("Chỉ nhận COD và MoMo.", doc.sections[0].content)

    def test_fingerprint_changes_with_the_source_url(self):
        doc = Document("d", "T", "store", None, "", [Section(0, "H", "x")])
        self.assertNotEqual(doc.fingerprint("a", "http://a/x"), doc.fingerprint("a", "http://b/x"))

    def test_sdk_accepts_numeric_customer_ids(self):
        from agent_server.auth import verify_customer_token
        token = sign_user_token(42, shop_id="demo_shop", secret="s")
        self.assertEqual(verify_customer_token(token, {"shop_id": "demo_shop", "chat_token_secret": "s"}), "42")


if __name__ == "__main__":
    unittest.main()
