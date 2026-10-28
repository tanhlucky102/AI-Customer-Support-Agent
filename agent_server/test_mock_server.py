"""Contract checks for the mock server's cross-site and identity boundaries."""

import base64
import hashlib
import hmac
import json
import time
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app import app
import knowledge_store
from mock_engine import TOKEN_SECRET


def encoded(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()


def token(customer_id: str, expires_in: int = 600, shop_id: str = "demo_shop",
          secret: str = TOKEN_SECRET) -> str:
    now = int(time.time())
    unsigned = encoded({"alg": "HS256", "typ": "JWT"}) + "." + encoded({
        "iss": shop_id, "sub": customer_id, "aud": "ati-agent",
        "iat": now, "exp": now + expires_in,
    })
    signature = hmac.new(secret.encode(), unsigned.encode(), hashlib.sha256).digest()
    return unsigned + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()


def reply_text(response: httpx.Response) -> str:
    chunks = []
    for block in response.text.split("\n\n"):
        lines = block.splitlines()
        if lines and lines[0] == "event: token":
            chunks.append(json.loads(lines[1].removeprefix("data: "))["text"])
    return "".join(chunks)


class MockServerContractTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.llm_patch = patch("llm_engine.AI_BOX_API_KEY", "")
        self.llm_patch.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(knowledge_store, "DB_PATH",
                                     Path(self.temp_dir.name) / "knowledge.db")
        self.db_patch.start()
        knowledge_store.init_db()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://localhost:8000")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.db_patch.stop()
        self.temp_dir.cleanup()
        self.llm_patch.stop()

    async def test_login_required_then_faq_stream_has_citation_and_done(self):
        body = {
            "shop_key": "pk_demo_shop", "message": "Phí giao hàng là bao nhiêu?"
        }
        self.assertEqual((await self.client.post("/v1/chat", json=body)).status_code, 401)
        self.assertEqual((await self.client.get("/v1/conversations",
                                                params={"shop_key": "pk_demo_shop"})).status_code, 401)
        response = await self.client.post("/v1/chat", json=body,
                                          headers={"Authorization": "Bearer " + token("C017")})
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertIn("event: token", response.text)
        self.assertIn("30.000₫", response.text)
        self.assertIn("event: citations", response.text)
        self.assertIn("event: done", response.text)
        history = await self.client.get("/v1/conversations", params={"shop_key": "pk_demo_shop"},
                                        headers={"Authorization": "Bearer " + token("C017")})
        self.assertEqual(len(history.json()["conversations"]), 1)

    async def test_frame_rejects_unregistered_parent_origin(self):
        response = await self.client.get("/widget/frame", params={
            "shop_key": "pk_demo_shop", "parent_origin": "https://unknown.example"
        })
        self.assertEqual(response.status_code, 403)
        response = await self.client.get("/widget/frame", params={
            "shop_key": "pk_demo_shop", "parent_origin": "http://localhost:8001"
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("frame-ancestors", response.headers["content-security-policy"])

    async def test_order_uses_customer_id_only_from_verified_token(self):
        order = {"id": "SO10293", "status": "shipping", "eta": "2026-10-12",
                 "total": 900000, "tracking_code": "VN123"}
        fake = httpx.Response(200, json=order,
                              request=httpx.Request("GET", "http://localhost:8001/api/v1/orders"))
        with patch("mock_engine.shop_get", new=AsyncMock(return_value=fake)) as shop_get:
            response = await self.client.post("/v1/chat", json={
                "shop_key": "pk_demo_shop", "message": "Đơn SO10293 của tôi"
            }, headers={"Authorization": "Bearer " + token("C017")})
            self.assertEqual(response.status_code, 200)
            self.assertIn("SO10293", response.text)
            shop_get.assert_awaited_once_with("/api/v1/customers/C017/orders/SO10293")

        forged = await self.client.post("/v1/chat", json={
            "shop_key": "pk_demo_shop", "message": "Đơn SO10293 của tôi", "customer_id": "C999"
        }, headers={"Authorization": "Bearer " + token("C017")})
        self.assertEqual(forged.status_code, 422)

    async def test_invalid_and_expired_tokens_are_rejected(self):
        body = {"shop_key": "pk_demo_shop", "message": "Đơn SO10293 của tôi"}
        for bad_token in (token("C017", -1), token("C017") + "x", "invalid"):
            response = await self.client.post("/v1/chat", json=body,
                                              headers={"Authorization": "Bearer " + bad_token})
            self.assertEqual(response.status_code, 401)

    async def test_llm_paraphrase_selects_only_shop_faq(self):
        with patch("llm_engine.AI_BOX_API_KEY", "test-key"), patch(
                "llm_engine.classify", new=AsyncMock(return_value={
                    "intent": "faq", "faq_ids": ["van-chuyen"]})):
            response = await self.client.post("/v1/chat", json={
                "shop_key": "pk_demo_shop", "message": "Mua món rẻ có tốn tiền ship không?"
            }, headers={"Authorization": "Bearer " + token("C017")})
        self.assertIn("30.000₫", response.text)
        self.assertIn('"mode": "llm"', response.text)
        self.assertIn("event: citations", response.text)

    async def test_llm_order_intent_accepts_number_without_so_but_uses_jwt_identity(self):
        order = {"id": "SO10293", "status": "shipping", "eta": "2026-10-12",
                 "total": 900000, "tracking_code": "VN123"}
        fake = httpx.Response(200, json=order,
                              request=httpx.Request("GET", "http://localhost:8001/api/v1/orders"))
        with patch("llm_engine.AI_BOX_API_KEY", "test-key"), patch(
                "llm_engine.classify", new=AsyncMock(return_value={
                    "intent": "order_id", "faq_ids": [], "order_id": "SO99999"})), patch(
                "mock_engine.shop_get", new=AsyncMock(return_value=fake)) as shop_get:
            response = await self.client.post("/v1/chat", json={
                "shop_key": "pk_demo_shop", "message": "Xem giúp đơn 10293"
            }, headers={"Authorization": "Bearer " + token("C017")})
        self.assertIn("SO10293", response.text)
        shop_get.assert_awaited_once_with("/api/v1/customers/C017/orders/SO10293")

    async def test_llm_out_of_scope_does_not_invent_answer(self):
        with patch("llm_engine.AI_BOX_API_KEY", "test-key"), patch(
                "llm_engine.classify", new=AsyncMock(return_value={
                    "intent": "other", "faq_ids": [], "order_id": None})):
            response = await self.client.post("/v1/chat", json={
                "shop_key": "pk_demo_shop", "message": "Thời tiết hôm nay thế nào?"
            }, headers={"Authorization": "Bearer " + token("C017")})
        self.assertIn("chưa xác định", response.text)
        self.assertIn("Thông tin liên hệ của shop", response.text)

    async def test_history_belongs_to_customer_and_can_continue_after_restart(self):
        headers = {"Authorization": "Bearer " + token("C017")}
        first = await self.client.post("/v1/chat", json={
            "shop_key": "pk_demo_shop", "message": "Phí giao hàng thế nào?"
        }, headers=headers)
        self.assertEqual(first.status_code, 200)
        done = next(json.loads(block.split("data: ", 1)[1]) for block in first.text.split("\n\n")
                    if block.startswith("event: done"))
        conversation_id = done["conversation_id"]
        knowledge_store.init_db()
        detail = await self.client.get(f"/v1/conversations/{conversation_id}",
                                       params={"shop_key": "pk_demo_shop"}, headers=headers)
        self.assertEqual([row["role"] for row in detail.json()["messages"]],
                         ["user", "assistant"])
        second = await self.client.post("/v1/chat", json={
            "shop_key": "pk_demo_shop", "message": "Bảo hành thế nào?",
            "conversation_id": conversation_id,
        }, headers=headers)
        self.assertEqual(second.status_code, 200)
        detail = await self.client.get(f"/v1/conversations/{conversation_id}",
                                       params={"shop_key": "pk_demo_shop"}, headers=headers)
        self.assertEqual(len(detail.json()["messages"]), 4)
        other_headers = {"Authorization": "Bearer " + token("C999")}
        self.assertEqual((await self.client.get(f"/v1/conversations/{conversation_id}",
                        params={"shop_key": "pk_demo_shop"}, headers=other_headers)).status_code, 404)
        self.assertEqual((await self.client.post("/v1/chat", json={
            "shop_key": "pk_demo_shop", "message": "tiếp tục", "conversation_id": conversation_id,
        }, headers=other_headers)).status_code, 404)

    async def test_llm_product_answer_uses_only_shop_api_facts(self):
        product = {"id": 12, "name": "iPhone 16 Pro Max 256GB - Đen", "price": 28000000,
                   "stock": 4, "storage_gb": 256, "ram_gb": 8, "screen": "6.9", "os": "iOS"}
        fake = httpx.Response(200, json=[product],
                              request=httpx.Request("GET", "http://localhost:8001/api/v1/products"))
        with patch("llm_engine.AI_BOX_API_KEY", "test-key"), patch(
                "llm_engine.classify", new=AsyncMock(return_value={
                    "intent": "product", "product_query": "iPhone 16 Pro Max 256GB",
                    "product_category": None, "max_price": None, "product_field": "stock"})), patch(
                "llm_engine.shop_get", new=AsyncMock(return_value=fake)) as shop_get:
            response = await self.client.post("/v1/chat", json={
                "shop_key": "pk_demo_shop", "message": "iPhone 16 Pro Max 256GB còn hàng không?"
            }, headers={"Authorization": "Bearer " + token("C017")})
        self.assertIn("còn 4 sản phẩm", response.text)
        self.assertIn("/products/12", response.text)
        self.assertIn("iPhone+16+Pro+Max+256GB", shop_get.await_args.args[0])

    async def test_llm_product_missing_fact_reports_uncertainty(self):
        with patch("llm_engine.AI_BOX_API_KEY", "test-key"), patch(
                "llm_engine.classify", new=AsyncMock(return_value={
                    "intent": "product", "product_query": "iPhone 16 Pro Max",
                    "product_category": None, "max_price": None, "product_field": "other"})), patch(
                "llm_engine.shop_get", new=AsyncMock(side_effect=AssertionError("should not call"))):
            response = await self.client.post("/v1/chat", json={
                "shop_key": "pk_demo_shop", "message": "iPhone 16 Pro Max chụp đêm tốt không?"
            }, headers={"Authorization": "Bearer " + token("C017")})
        self.assertIn("liên hệ trực tiếp", response.text)

    async def test_llm_smalltalk_stays_in_support_role(self):
        with patch("llm_engine.AI_BOX_API_KEY", "test-key"), patch(
                "llm_engine.classify", new=AsyncMock(return_value={
                    "intent": "smalltalk", "faq_ids": []})):
            response = await self.client.post("/v1/chat", json={
                "shop_key": "pk_demo_shop", "message": "Cảm ơn nhé"
            }, headers={"Authorization": "Bearer " + token("C017")})
        self.assertIn("Rất vui được hỗ trợ", response.text)

    async def test_greeting_sale_and_event_use_only_shop_facts(self):
        auth = {"Authorization": "Bearer " + token("C017")}
        offers = {
            "coupons": [{"code": "GIAM50K", "description": "Giảm 50.000₫ cho đơn từ 500.000₫"}],
            "discounted_products": [{"id": 12, "name": "Điện thoại A",
                                     "price": 450000, "old_price": 500000}],
            "event_banner": {"title": "HOT SALE CUỐI TUẦN", "url": "/"},
            "events_status": "not_configured", "current_events": [], "upcoming_events": [],
        }
        fake = httpx.Response(200, json=offers, request=httpx.Request("GET", "http://shop/api/v1/offers"))
        decisions = {
            "Chào shop": {"intent": "smalltalk"},
            "Shop đang có sale gì không?": {"intent": "offers", "offer_topic": "sale", "offer_field": "list"},
            "Shop có sự kiện gì sắp tới?": {"intent": "offers", "offer_topic": "event", "offer_field": "list"},
        }

        async def classify_message(message, *_):
            return decisions[message]

        with patch("llm_engine.AI_BOX_API_KEY", "test-key"), patch(
                "llm_engine.classify", new=AsyncMock(side_effect=classify_message)), patch(
                "llm_engine.shop_get", new=AsyncMock(return_value=fake)) as shop_get:
            greeting = await self.client.post("/v1/chat", json={
                "shop_key": "pk_demo_shop", "message": "Chào shop"}, headers=auth)
            sale = await self.client.post("/v1/chat", json={
                "shop_key": "pk_demo_shop", "message": "Shop đang có sale gì không?"}, headers=auth)
            event = await self.client.post("/v1/chat", json={
                "shop_key": "pk_demo_shop", "message": "Shop có sự kiện gì sắp tới?"}, headers=auth)
        self.assertIn("Chào bạn", reply_text(greeting))
        self.assertIn("ưu đãi", reply_text(greeting))
        self.assertIn("GIAM50K", reply_text(sale))
        self.assertIn("500.000₫", reply_text(sale))
        self.assertIn("Điện thoại A", reply_text(sale))
        self.assertNotIn("giảm đến 50%", reply_text(sale).lower())
        self.assertIn("chưa có thời gian hoặc điều kiện", reply_text(event))
        self.assertIn("HOT SALE CUỐI TUẦN", reply_text(event))
        self.assertEqual(shop_get.await_count, 2)

    async def test_politics_is_refused_before_model_or_shop_api(self):
        with patch("llm_engine.AI_BOX_API_KEY", "test-key"), patch(
                "llm_engine.classify", new=AsyncMock(side_effect=AssertionError("no model call"))), patch(
                "llm_engine.shop_get", new=AsyncMock(side_effect=AssertionError("no shop call"))):
            response = await self.client.post("/v1/chat", json={
                "shop_key": "pk_demo_shop", "message": "Tình hình chính trị hiện nay thế nào?"},
                headers={"Authorization": "Bearer " + token("C017")})
        self.assertEqual(response.status_code, 200)
        self.assertIn("không trao đổi về chủ đề này", reply_text(response))
        self.assertIn('"mode": "policy"', response.text)

    async def test_external_shop_publishes_offers_with_dates_and_isolation(self):
        knowledge_store.create_shop(
            shop_id="other_shop", public_key="pk_other", faq_write_key="private-write-key",
            title="Other shop", greeting="Hello", primary_color="#123456",
            shop_url="http://localhost:8002", allowed_origins=["http://localhost:8002"],
            chat_token_secret="other-token-secret", suggestions=[])
        path = "/v1/shops/other_shop/offers"
        now = datetime.now(timezone.utc)
        upcoming = now + timedelta(days=1)
        ending = now + timedelta(days=2)
        body = {"offers": [
            {"id": "weekend-sale", "kind": "promotion", "title": "Giảm cuối tuần",
             "description": "Giảm 10% cho phụ kiện", "source_url": "http://localhost:8002/sale"},
            {"id": "launch", "kind": "event", "title": "Ra mắt cửa hàng",
             "description": "Giới thiệu sản phẩm mới", "starts_at": upcoming.isoformat(),
             "ends_at": ending.isoformat(), "source_url": "http://localhost:8002/events/launch"},
        ]}
        self.assertEqual((await self.client.put(path, json=body)).status_code, 401)
        bad = {"offers": [{**body["offers"][1], "source_url": "https://wrong.example/event"}]}
        self.assertEqual((await self.client.put(path, json=bad, headers={
            "Authorization": "Bearer private-write-key"})).status_code, 422)
        saved = await self.client.put(path, json=body, headers={
            "Authorization": "Bearer private-write-key"})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["count"], 2)
        stored = await self.client.get(path, headers={
            "Authorization": "Bearer private-write-key"})
        self.assertEqual(len(stored.json()["offers"]), 2)
        knowledge_store.init_db()
        auth = {"Authorization": "Bearer " + token("X1", shop_id="other_shop",
                                                   secret="other-token-secret")}
        with patch("llm_engine.AI_BOX_API_KEY", "test-key"), patch(
                "llm_engine.classify", new=AsyncMock(side_effect=[
                    {"intent": "offers", "offer_topic": "sale", "offer_field": "list"},
                    {"intent": "offers", "offer_topic": "event", "offer_field": "list"}])):
            sale = await self.client.post("/v1/chat", json={
                "shop_key": "pk_other", "message": "Có sale gì?"}, headers=auth)
            event = await self.client.post("/v1/chat", json={
                "shop_key": "pk_other", "message": "Có sự kiện nào?"}, headers=auth)
        self.assertIn("Giảm 10% cho phụ kiện", reply_text(sale))
        self.assertIn("Sắp diễn ra", reply_text(event))
        self.assertIn("Ra mắt cửa hàng", reply_text(event))
        demo = knowledge_store.list_offers("demo_shop")
        self.assertEqual(demo["revision"], 0)

    async def test_external_shop_without_offer_snapshot_reports_unknown(self):
        knowledge_store.create_shop(
            shop_id="other_shop", public_key="pk_other", faq_write_key="private-write-key",
            title="Other shop", greeting="Hello", primary_color="#123456",
            shop_url="http://localhost:8002", allowed_origins=["http://localhost:8002"],
            chat_token_secret="other-token-secret", suggestions=[])
        with patch("llm_engine.AI_BOX_API_KEY", "test-key"), patch(
                "llm_engine.classify", new=AsyncMock(return_value={
                    "intent": "offers", "offer_topic": "event", "offer_field": "list"})):
            response = await self.client.post("/v1/chat", json={
                "shop_key": "pk_other", "message": "Shop có sự kiện không?"},
                headers={"Authorization": "Bearer " + token("X1", shop_id="other_shop",
                                                               secret="other-token-secret")})
        self.assertIn("chưa đồng bộ", response.text)
        self.assertIn("liên hệ trực tiếp", response.text)

    async def test_external_shop_pushes_faq_then_widget_reads_local_copy(self):
        knowledge_store.create_shop(
            shop_id="other_shop", public_key="pk_other", faq_write_key="private-write-key",
            title="Other shop", greeting="Hello", primary_color="#123456",
            shop_url="http://localhost:8002",
            allowed_origins=["http://localhost:8002"], chat_token_secret="other-token-secret",
            suggestions=[])
        body = {"faqs": [{
            "id": "hours", "question": "Giờ mở cửa là gì?",
            "answer": "Mở cửa từ 8 giờ đến 17 giờ.", "keywords": ["giờ mở cửa"],
            "source_url": "http://localhost:8002/"
        }]}
        path = "/v1/shops/other_shop/faqs"
        self.assertEqual((await self.client.put(path, json=body)).status_code, 401)
        self.assertEqual((await self.client.put(path, json=body,
            headers={"Authorization": "Bearer wrong"})).status_code, 401)
        saved = await self.client.put(path, json=body,
                                      headers={"Authorization": "Bearer private-write-key"})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["count"], 1)
        self.assertEqual((await self.client.get(path)).status_code, 401)
        stored = await self.client.get(path,
                                       headers={"Authorization": "Bearer private-write-key"})
        self.assertEqual(stored.json()["faqs"], body["faqs"])
        knowledge_store.init_db()  # Simulate server restart; synced FAQ remains.
        wrong_shop_token = await self.client.post("/v1/chat", json={
            "shop_key": "pk_other", "message": "Giờ mở cửa?"
        }, headers={"Authorization": "Bearer " + token("C017")})
        self.assertEqual(wrong_shop_token.status_code, 401)
        with patch("mock_engine.shop_get", new=AsyncMock(side_effect=AssertionError("unexpected shop call"))):
            reply = await self.client.post("/v1/chat", json={
                "shop_key": "pk_other", "message": "Giờ mở cửa?"
            }, headers={"Authorization": "Bearer " + token("X1", shop_id="other_shop",
                                                       secret="other-token-secret")})
        self.assertEqual(reply.status_code, 200)
        self.assertIn("Mở cửa từ 8 giờ", reply.text)
        self.assertIn("event: citations", reply.text)
        demo_reply = await self.client.post("/v1/chat", json={
            "shop_key": "pk_demo_shop", "message": "Giờ mở cửa?"
        }, headers={"Authorization": "Bearer " + token("C017")})
        self.assertNotIn("Mở cửa từ 8 giờ", demo_reply.text)

        bad_source = {"faqs": [{**body["faqs"][0], "source_url": "https://evil.example/"}]}
        rejected = await self.client.put(path, json=bad_source,
                                         headers={"Authorization": "Bearer private-write-key"})
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(len(knowledge_store.list_faqs("other_shop")), 1)
        cleared = await self.client.put(path, json={"faqs": []},
                                        headers={"Authorization": "Bearer private-write-key"})
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(knowledge_store.list_faqs("other_shop"), [])


if __name__ == "__main__":
    unittest.main()
