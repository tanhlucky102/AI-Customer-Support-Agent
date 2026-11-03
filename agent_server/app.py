"""Agent Server: embeddable widget, shop-scoped content APIs and the streaming agent chat API.

Run from the repository root:  uvicorn agent_server.app:app --port 8000
"""

import asyncio
import json
import logging
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_server import agent
from agent_server.auth import verify_customer_claims
from agent_server.commerce import adapter_for
from agent_server.rate_limit import ChatLimiter
from agent_server.store import (append_turn, conversation_owned, create_conversation,
                                delete_empty_conversation, demo_shop_enabled, guest_allowed, init_db,
                                list_conversations, list_faqs, list_messages, list_offers,
                                list_tickets, origin_of, replace_faqs, replace_offers,
                                revoke_customer_tokens, save_trace, shop_by_id, shop_by_public_key,
                                token_revoked, verify_faq_key)
from agent_server.tools import TurnContext
from rag.search import search
from shared.llm import LLMProvider
from shared.pii import PiiFilter, mask_text
from shared.settings import ROOT, env, env_flag, env_int

WIDGET_DIR = ROOT / "widget"
logger = logging.getLogger(__name__)
logger.addFilter(PiiFilter())
# Public origin when the server sits behind a tunnel or proxy that rewrites the Host header.
AGENT_ORIGIN = origin_of(env("AGENT_SERVER_URL"))
# Upper bound for one chat turn, checked between events (each model call, shop call and search
# has its own timeout), so a slow gateway cannot hold the conversation lock indefinitely.
TURN_TIMEOUT = env_int("TURN_TIMEOUT_SECONDS", 90)


def warm_up() -> None:
    """Load the embedding model and reranker before the first question arrives."""
    try:
        from rag.models import embedder, reranker
        embedder().encode(["khởi động"])
        if env_flag("RAG_RERANK", True):
            reranker().score("khởi động", ["khởi động"])
    except Exception:
        logger.exception("RAG warm-up failed; models load on first use")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    missing = [name for name in ("DEMO_SHOP_API_KEY", "DEMO_SHOP_TOKEN_SECRET") if not env(name)]
    if demo_shop_enabled() and missing:
        print("[agent_server] WARNING: " + ", ".join(missing) + " missing in .env; demo shop chat "
              "tokens are rejected and shop API calls fail until both servers share these keys.")
    if env_flag("RAG_WARMUP", True):
        threading.Thread(target=warm_up, daemon=True).start()
    yield


app = FastAPI(title="ATI Agent Server", version="1.0.0", lifespan=lifespan)
feedback_counts = {"yes": 0, "no": 0}  # In-memory and reset whenever the server restarts.
llm = LLMProvider()


class ConversationLocks:
    """One lock per conversation, so a second message waits until the first turn is saved."""

    def __init__(self) -> None:
        self.locks: dict[str, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def hold(self, conversation_id: str):
        lock, users = self.locks.get(conversation_id, (asyncio.Lock(), 0))
        self.locks[conversation_id] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            lock, users = self.locks[conversation_id]
            if users == 1:
                del self.locks[conversation_id]
            else:
                self.locks[conversation_id] = (lock, users - 1)


conversation_locks = ConversationLocks()
chat_limiter = ChatLimiter(
    per_customer_per_minute=env_int("RATE_LIMIT_PER_SESSION_PER_MINUTE", 10),
    per_ip_per_minute=env_int("RATE_LIMIT_PER_IP_PER_MINUTE", 30),
    daily_per_shop=env_int("DAILY_CHAT_QUOTA_PER_SHOP", 500),
)


class ChatRequest(BaseModel):
    # customer_id must never be accepted from the browser.
    model_config = ConfigDict(extra="forbid")
    shop_key: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    conversation_id: str | None = Field(default=None, max_length=100)

    @field_validator("message")
    @classmethod
    def printable(cls, value: str) -> str:
        """Drop control characters (Postgres rejects NUL, after a whole answer was streamed)."""
        return "".join(char for char in value if char in "\n\t" or ord(char) >= 32)


class LogoutRequest(BaseModel):
    shop_key: str = Field(min_length=1, max_length=100)


class ShopLogout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: str = Field(min_length=1, max_length=200)
    logout_at: int | None = None  # epoch seconds on the shop's clock, the clock that sets `iat`


class FeedbackRequest(BaseModel):
    shop_key: str
    conversation_id: str
    rating: str


class FaqItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    question: str = Field(min_length=3, max_length=300)
    answer: str = Field(min_length=1, max_length=3000)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    source_url: str | None = Field(default=None, max_length=1000)


class FaqSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    faqs: list[FaqItem] = Field(max_length=500)


class OfferItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    kind: Literal["promotion", "event"]
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=3, max_length=3000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    source_url: str | None = Field(default=None, max_length=1000)


class OfferSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offers: list[OfferItem] = Field(max_length=100)


def require_shop_key(shop_key: str) -> dict:
    shop = shop_by_public_key(shop_key)
    if not shop:
        raise HTTPException(404, "unknown_shop_key")
    return shop


def require_write_key(shop_id: str, authorization: str | None) -> None:
    scheme, _, key = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not key or not verify_faq_key(shop_id, key):
        raise HTTPException(401, "invalid_faq_write_key", headers={"WWW-Authenticate": "Bearer"})


GUEST_ID = re.compile(r"[0-9a-f]{32}")


def require_customer(shop: dict, authorization: str | None, guest_id: str | None = None) -> str:
    """The customer from the shop-signed token; without one, a guest of a shop that allows guests.

    A guest ID is a random value the widget keeps in the browser. It is not a login: it only
    groups the guest's own conversations, and guest shops have no order or account tools."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() == "bearer" and token:
        claims = verify_customer_claims(token, shop)
        if not claims:
            raise HTTPException(401, "invalid_chat_token", headers={"WWW-Authenticate": "Bearer"})
        if token_revoked(shop["shop_id"], claims["sub"], claims["iat"]):  # logged out since it was issued
            raise HTTPException(401, "chat_token_revoked", headers={"WWW-Authenticate": "Bearer"})
        return claims["sub"]
    if guest_id and guest_allowed(shop) and GUEST_ID.fullmatch(guest_id):
        return f"guest:{guest_id}"
    raise HTTPException(401, "login_required", headers={"WWW-Authenticate": "Bearer"})


def require_widget_origin(request: Request) -> None:
    """Browsers send Origin on POST; only the widget iframe, served from this server, may call."""
    origin = request.headers.get("origin")
    if origin and origin not in (f"{request.url.scheme}://{request.url.netloc}", AGENT_ORIGIN):
        raise HTTPException(403, "origin_not_allowed")


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def mode() -> str:
    return "agent" if llm.enabled else "fallback"


@app.get("/healthz")
async def healthz():
    return {"ok": True, "mode": mode(), "model": llm.model if llm.enabled else None}


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
    allowed_origins = shop["allowed_origins"]
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
    return {
        "shop_id": shop["shop_id"],
        "title": shop["title"],
        "greeting": shop["greeting"],
        "primary_color": shop["primary_color"],
        "suggestions": shop["suggestions"],
        "shop_url": shop["shop_url"],
        "mode": mode(),
        "guest_chat": guest_allowed(shop),
    }


async def reindex_faqs(shop: dict, faqs: list[dict]) -> bool:
    """Embed the pushed FAQ in a worker thread so live chat streams are not frozen meanwhile."""
    from rag.ingest import index_faqs
    try:
        await asyncio.to_thread(lambda: asyncio.run(index_faqs(shop["shop_id"], shop["shop_url"], faqs)))
        return True
    except Exception:
        logger.exception("FAQ indexing failed for shop %s", shop["shop_id"])
        return False


@app.put("/v1/shops/{shop_id}/faqs")
async def sync_faqs(shop_id: str, payload: FaqSnapshot,
                    authorization: str | None = Header(default=None)):
    """A customer's backend pushes its complete FAQ snapshot; it is indexed for search."""
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
    indexed = await reindex_faqs(shop, faqs)
    return {"shop_id": shop_id, "count": count, "revision": revision, "indexed": indexed}


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


@app.get("/v1/shops/{shop_id}/tickets")
async def get_tickets(shop_id: str, authorization: str | None = Header(default=None)):
    """Handover tickets for the shop's staff (same write key as content pushes)."""
    require_write_key(shop_id, authorization)
    return {"shop_id": shop_id, "tickets": list_tickets(shop_id)}


TRACE_ARG_LIMIT = 80


def trace_tools(calls: list[dict]) -> list[dict]:
    """Tool names, success and masked short arguments: never outputs or free-text summaries."""
    safe = []
    for call in calls:
        args = {key: mask_text(str(value))[:TRACE_ARG_LIMIT] for key, value in call["args"].items()
                if key != "transcript_summary"}
        safe.append({"name": call["name"], "args": args, "ok": call["ok"]})
    return safe


def turn_context(shop: dict, customer_id: str, conversation_id: str) -> TurnContext:
    return TurnContext(shop=shop, customer_id=customer_id, conversation_id=conversation_id,
                       turn_key=f"{conversation_id}:{uuid.uuid4().hex}", adapter=adapter_for(shop),
                       search=search)


@app.post("/v1/chat")
async def chat(payload: ChatRequest, request: Request,
               authorization: str | None = Header(default=None),
               x_ati_guest: str | None = Header(default=None)):
    require_widget_origin(request)
    shop = require_shop_key(payload.shop_key)
    customer_id = require_customer(shop, authorization, x_ati_guest)

    message = payload.message.strip()
    if not message:
        raise HTTPException(400, "empty_message")
    conversation_id = payload.conversation_id or uuid.uuid4().hex
    if payload.conversation_id and not conversation_owned(
            conversation_id, shop["shop_id"], customer_id):
        raise HTTPException(404, "unknown_conversation")
    limited = chat_limiter.check(shop["shop_id"], customer_id,
                                 request.client.host if request.client else "unknown")
    if limited:
        reason, retry_after = limited
        raise HTTPException(429, reason, headers={"Retry-After": str(retry_after)})

    async def events():
        started = time.perf_counter()
        first_ms = None
        trace = {"conversation_id": conversation_id, "shop_id": shop["shop_id"], "mode": "error"}
        created = False
        try:
            async with conversation_locks.hold(conversation_id):
                history = (await asyncio.to_thread(list_messages, conversation_id, shop["shop_id"],
                                                   customer_id, 8) if payload.conversation_id else [])
                if not payload.conversation_id:
                    await asyncio.to_thread(create_conversation, conversation_id, shop["shop_id"],
                                            customer_id, message)
                    created = True
                ctx = turn_context(shop, customer_id, conversation_id)
                done = None
                async for event in agent.run(ctx, history or [], message, llm=llm):
                    if time.perf_counter() - started > TURN_TIMEOUT:
                        raise TimeoutError("chat turn took too long")
                    if first_ms is None:
                        first_ms = round((time.perf_counter() - started) * 1000)
                    kind = event["type"]
                    if kind == "done":
                        done = event
                    elif kind == "error":
                        raise RuntimeError(event.get("detail") or event["message"])
                    else:
                        yield sse(kind, {key: value for key, value in event.items() if key != "type"})
                message_id = await asyncio.to_thread(append_turn, conversation_id, shop["shop_id"],
                                                     customer_id, message, done["reply"], done["sources"])
                tools_called = trace_tools(done["tools"])
                if done.get("fallback_reason"):  # Why the model was not used, for operators.
                    trace["error"] = f"fallback: {done['fallback_reason']}"[:300]
                trace.update(message_id=message_id, mode=done["mode"], model=done.get("model"),
                             llm_calls=done["llm_calls"], tools_called=tools_called,
                             latency_first_token_ms=done.get("first_token_ms"),
                             **{key: done["usage"][key] for key in
                                ("input_tokens", "output_tokens", "cached_tokens")})
        except Exception as error:
            # Contract C4: end with an error event instead of a silently cut stream.
            # The question itself is not logged because it may contain personal data.
            logger.exception("chat failed for shop %s", shop["shop_id"])
            trace["error"] = type(error).__name__
            if created:  # Do not leave an empty conversation in the customer's history.
                try:
                    await asyncio.to_thread(delete_empty_conversation, conversation_id)
                except Exception:
                    logger.exception("empty conversation not removed")
            yield sse("error", {"message": "Chatbot chưa xử lý được câu hỏi. Vui lòng thử lại sau."})
            return
        finally:
            trace.update(latency_first_ms=first_ms,
                         latency_total_ms=round((time.perf_counter() - started) * 1000))
            try:
                await asyncio.to_thread(save_trace, trace)
            except Exception:
                logger.exception("trace not saved")
        yield sse("done", {"conversation_id": conversation_id, "mode": done["mode"]})

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no",
    })


@app.get("/v1/conversations")
async def conversations(shop_key: str, authorization: str | None = Header(default=None),
                        x_ati_guest: str | None = Header(default=None)):
    shop = require_shop_key(shop_key)
    customer_id = require_customer(shop, authorization, x_ati_guest)
    return JSONResponse({"conversations": list_conversations(shop["shop_id"], customer_id)},
                        headers={"Cache-Control": "no-store"})


@app.get("/v1/conversations/{conversation_id}")
async def conversation(conversation_id: str, shop_key: str,
                       authorization: str | None = Header(default=None),
                       x_ati_guest: str | None = Header(default=None)):
    shop = require_shop_key(shop_key)
    customer_id = require_customer(shop, authorization, x_ati_guest)
    messages = list_messages(conversation_id, shop["shop_id"], customer_id)
    if messages is None:
        raise HTTPException(404, "unknown_conversation")
    return JSONResponse({"conversation_id": conversation_id, "messages": messages},
                        headers={"Cache-Control": "no-store"})


@app.post("/v1/logout")
async def logout(payload: LogoutRequest, request: Request,
                 authorization: str | None = Header(default=None)):
    """The widget, when the shop page calls ATI.reset() on logout: the customer's chat tokens issued
    until now, including the one presented, stop working at once."""
    require_widget_origin(request)
    shop = require_shop_key(payload.shop_key)
    scheme, _, token = (authorization or "").partition(" ")
    claims = verify_customer_claims(token, shop) if scheme.lower() == "bearer" and token else None
    if not claims:
        raise HTTPException(401, "invalid_chat_token", headers={"WWW-Authenticate": "Bearer"})
    # max(): the token itself is cut off even if the shop's clock runs ahead of this server's.
    revoke_customer_tokens(shop["shop_id"], claims["sub"], max(int(time.time()), claims["iat"]))
    return {"ok": True}


@app.post("/v1/shops/{shop_id}/logout")
async def shop_logout(shop_id: str, payload: ShopLogout, authorization: str | None = Header(default=None)):
    """The shop's backend, when a customer logs out (sdk: revoke_user_tokens): every chat token issued
    to that customer until now stops working, on every device."""
    require_write_key(shop_id, authorization)
    now = int(time.time())
    logout_at = min(payload.logout_at, now + 300) if payload.logout_at else now
    revoke_customer_tokens(shop_id, payload.customer_id, logout_at)
    return {"ok": True}


@app.post("/v1/feedback")
async def feedback(payload: FeedbackRequest, request: Request,
                   authorization: str | None = Header(default=None),
                   x_ati_guest: str | None = Header(default=None)):
    require_widget_origin(request)
    shop = require_shop_key(payload.shop_key)
    customer_id = require_customer(shop, authorization, x_ati_guest)
    if not conversation_owned(payload.conversation_id, shop["shop_id"], customer_id):
        raise HTTPException(404, "unknown_conversation")
    if payload.rating not in feedback_counts:
        raise HTTPException(400, "invalid_rating")
    feedback_counts[payload.rating] += 1
    return {"ok": True, "stored": "memory_only"}


@app.get("/v1/demo-stats")
async def demo_stats():
    return {"mode": mode(), "feedback": feedback_counts.copy()}
