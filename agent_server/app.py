"""Third-party widget prototype with a shop-scoped FAQ sync API."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from knowledge_store import (append_turn, conversation_owned, create_conversation,
                             init_db, list_conversations, list_faqs, list_messages,
                             list_offers, origin_of, replace_faqs, replace_offers,
                             shop_by_id, shop_by_public_key,
                             verify_faq_key)
from llm_engine import AI_BOX_MODEL, answer, enabled
from mock_engine import verify_customer_token


ROOT = Path(__file__).resolve().parent.parent
WIDGET_DIR = ROOT / "widget"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="ATI Widget Test Server", version="0.2.0", lifespan=lifespan)
feedback_counts = {"yes": 0, "no": 0}  # In-memory and reset whenever this test server restarts.


class ChatRequest(BaseModel):
    shop_key: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    conversation_id: str | None = Field(default=None, max_length=100)

    class Config:
        extra = "forbid"  # customer_id must never be accepted from the browser.


class FeedbackRequest(BaseModel):
    shop_key: str
    conversation_id: str
    rating: str


class FaqItem(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    question: str = Field(min_length=3, max_length=300)
    answer: str = Field(min_length=1, max_length=3000)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    source_url: str | None = Field(default=None, max_length=1000)

    class Config:
        extra = "forbid"


class FaqSnapshot(BaseModel):
    faqs: list[FaqItem] = Field(max_length=500)

    class Config:
        extra = "forbid"


class OfferItem(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    kind: Literal["promotion", "event"]
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=3, max_length=3000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    source_url: str | None = Field(default=None, max_length=1000)

    class Config:
        extra = "forbid"


class OfferSnapshot(BaseModel):
    offers: list[OfferItem] = Field(max_length=100)

    class Config:
        extra = "forbid"


def require_shop_key(shop_key: str) -> dict:
    shop = shop_by_public_key(shop_key)
    if not shop:
        raise HTTPException(404, "unknown_shop_key")
    return shop


def require_write_key(shop_id: str, authorization: str | None) -> None:
    scheme, _, key = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not key or not verify_faq_key(shop_id, key):
        raise HTTPException(401, "invalid_faq_write_key", headers={"WWW-Authenticate": "Bearer"})


def require_customer(shop: dict, authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "login_required", headers={"WWW-Authenticate": "Bearer"})
    customer_id = verify_customer_token(token, shop)
    if not customer_id:
        raise HTTPException(401, "invalid_chat_token", headers={"WWW-Authenticate": "Bearer"})
    return customer_id


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/healthz")
async def healthz():
    return {"ok": True, "mode": "llm" if enabled() else "mock",
            "model": AI_BOX_MODEL if enabled() else None}


@app.get("/widget/loader.js")
async def loader():
    return FileResponse(WIDGET_DIR / "loader.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/widget/loader.css")
async def loader_css():
    return FileResponse(WIDGET_DIR / "loader.css", media_type="text/css",
                        headers={"Cache-Control": "no-cache"})


@app.get("/widget/frame")
async def frame(shop_key: str = Query(...), parent_origin: str = Query(...)):
    shop = require_shop_key(shop_key)
    allowed_origins = json.loads(shop["allowed_origins"])
    if parent_origin not in allowed_origins:
        raise HTTPException(403, "origin_not_allowed")
    policy = ("default-src 'self'; script-src 'self'; style-src 'self'; "
              "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
              "form-action 'none'; frame-ancestors " + " ".join(allowed_origins))
    return HTMLResponse((WIDGET_DIR / "frame.html").read_text(encoding="utf-8"), headers={
        "Content-Security-Policy": policy,
        "Cache-Control": "no-store",
    })


@app.get("/widget/frame.js")
async def frame_js():
    return FileResponse(WIDGET_DIR / "frame.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/widget/frame.css")
async def frame_css():
    return FileResponse(WIDGET_DIR / "frame.css", media_type="text/css",
                        headers={"Cache-Control": "no-cache"})


@app.get("/v1/widget-config")
async def widget_config(shop_key: str):
    shop = require_shop_key(shop_key)
    greeting = shop["greeting"]
    if enabled() and shop["shop_id"] == "demo_shop":
        greeting = "Xin chào! Mình hỗ trợ sản phẩm, ưu đãi, sự kiện, chính sách và đơn hàng."
    return {
        "shop_id": shop["shop_id"],
        "title": shop["title"],
        "greeting": greeting,
        "primary_color": shop["primary_color"],
        "suggestions": json.loads(shop["suggestions"]),
        "shop_url": shop["shop_url"],
        "mode": "llm" if enabled() else "mock",
    }


@app.put("/v1/shops/{shop_id}/faqs")
async def sync_faqs(shop_id: str, payload: FaqSnapshot,
                    authorization: str | None = Header(default=None)):
    """A customer's backend pushes its complete FAQ snapshot to our server."""
    require_write_key(shop_id, authorization)
    shop = shop_by_id(shop_id)
    if not shop:
        raise HTTPException(404, "unknown_shop")
    faqs = [faq.model_dump() for faq in payload.faqs]
    if len({faq["id"] for faq in faqs}) != len(faqs):
        raise HTTPException(422, "duplicate_faq_id")
    expected_origin = origin_of(shop["shop_url"])
    for faq in faqs:
        faq["question"] = faq["question"].strip()
        faq["answer"] = faq["answer"].strip()
        faq["keywords"] = [keyword.strip() for keyword in faq["keywords"]]
        if not faq["question"] or not faq["answer"]:
            raise HTTPException(422, "empty_faq_field")
        if any(not keyword or len(keyword) > 120 for keyword in faq["keywords"]):
            raise HTTPException(422, "invalid_keyword")
        if faq["source_url"] and origin_of(faq["source_url"]) != expected_origin:
            raise HTTPException(422, "source_url_must_match_shop_origin")
    count, revision = replace_faqs(shop_id, faqs)
    return {"shop_id": shop_id, "count": count, "revision": revision}


@app.get("/v1/shops/{shop_id}/faqs")
async def get_faqs(shop_id: str, authorization: str | None = Header(default=None)):
    require_write_key(shop_id, authorization)
    return {"shop_id": shop_id, "faqs": list_faqs(shop_id)}


@app.put("/v1/shops/{shop_id}/offers")
async def sync_offers(shop_id: str, payload: OfferSnapshot,
                      authorization: str | None = Header(default=None)):
    """A customer's backend publishes promotions and dated events for this shop."""
    require_write_key(shop_id, authorization)
    shop = shop_by_id(shop_id)
    if not shop:
        raise HTTPException(404, "unknown_shop")
    offers = [item.model_dump(mode="json") for item in payload.offers]
    if len({item["id"] for item in offers}) != len(offers):
        raise HTTPException(422, "duplicate_offer_id")
    expected_origin = origin_of(shop["shop_url"])
    for item, original in zip(offers, payload.offers):
        item["title"] = item["title"].strip()
        item["description"] = item["description"].strip()
        start, end = original.starts_at, original.ends_at
        if not item["title"] or not item["description"]:
            raise HTTPException(422, "empty_offer_field")
        if item["kind"] == "event" and (start is None or end is None):
            raise HTTPException(422, "event_dates_required")
        if (start is None) != (end is None):
            raise HTTPException(422, "both_offer_dates_required")
        if start and (not start.tzinfo or not end.tzinfo or end <= start):
            raise HTTPException(422, "invalid_offer_dates")
        if item["source_url"] and origin_of(item["source_url"]) != expected_origin:
            raise HTTPException(422, "source_url_must_match_shop_origin")
    count, revision = replace_offers(shop_id, offers)
    return {"shop_id": shop_id, "count": count, "revision": revision}


@app.get("/v1/shops/{shop_id}/offers")
async def get_offers(shop_id: str, authorization: str | None = Header(default=None)):
    require_write_key(shop_id, authorization)
    return {"shop_id": shop_id, **list_offers(shop_id)}


@app.post("/v1/chat")
async def chat(payload: ChatRequest, authorization: str | None = Header(default=None)):
    shop = require_shop_key(payload.shop_key)
    customer_id = require_customer(shop, authorization)

    message = payload.message.strip()
    if not message:
        raise HTTPException(400, "empty_message")
    conversation_id = payload.conversation_id or uuid.uuid4().hex
    if payload.conversation_id and not conversation_owned(
            conversation_id, shop["shop_id"], customer_id):
        raise HTTPException(404, "unknown_conversation")

    async def events():
        yield sse("status", {"message": "Đang xử lý câu hỏi..."})
        history = (list_messages(conversation_id, shop["shop_id"], customer_id, limit=8)
                   if payload.conversation_id else [])
        status, reply, sources, mode = await answer(message, customer_id, shop, history or [])
        if not payload.conversation_id:
            create_conversation(conversation_id, shop["shop_id"], customer_id, message)
        append_turn(conversation_id, shop["shop_id"], customer_id, message, reply, sources)
        yield sse("status", {"message": status})
        # Replies are chunked for the widget; LLM intent routing is not token streaming.
        for start in range(0, len(reply), 35):
            yield sse("token", {"text": reply[start:start + 35]})
            await asyncio.sleep(0.025)
        if sources:
            yield sse("citations", {"sources": sources})
        yield sse("done", {"conversation_id": conversation_id, "mode": mode})

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no",
    })


@app.get("/v1/conversations")
async def conversations(shop_key: str, authorization: str | None = Header(default=None)):
    shop = require_shop_key(shop_key)
    customer_id = require_customer(shop, authorization)
    return JSONResponse({"conversations": list_conversations(shop["shop_id"], customer_id)},
                        headers={"Cache-Control": "no-store"})


@app.get("/v1/conversations/{conversation_id}")
async def conversation(conversation_id: str, shop_key: str,
                       authorization: str | None = Header(default=None)):
    shop = require_shop_key(shop_key)
    customer_id = require_customer(shop, authorization)
    messages = list_messages(conversation_id, shop["shop_id"], customer_id)
    if messages is None:
        raise HTTPException(404, "unknown_conversation")
    return JSONResponse({"conversation_id": conversation_id, "messages": messages},
                        headers={"Cache-Control": "no-store"})


@app.post("/v1/feedback")
async def feedback(payload: FeedbackRequest,
                   authorization: str | None = Header(default=None)):
    shop = require_shop_key(payload.shop_key)
    customer_id = require_customer(shop, authorization)
    if not conversation_owned(payload.conversation_id, shop["shop_id"], customer_id):
        raise HTTPException(404, "unknown_conversation")
    if payload.rating not in feedback_counts:
        raise HTTPException(400, "invalid_rating")
    feedback_counts[payload.rating] += 1
    return {"ok": True, "stored": "memory_only"}


@app.get("/v1/demo-stats")
async def demo_stats():
    return {"mode": "llm" if enabled() else "mock", "feedback": feedback_counts.copy()}
