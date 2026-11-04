"""Agent Server: embeddable widget, shop-scoped content APIs and the streaming agent chat API.

Run from the repository root:  uvicorn agent_server.app:app --port 8000

What turns share lives in PostgreSQL, so several server processes (uvicorn --workers N, or several
containers behind a load balancer) act as one: the lock that runs one turn of a conversation at a
time (conversations.busy_until), the chat limits (rate_events) and the one-push-at-a-time lock of a
shop's FAQ (an advisory lock). The only per-process state is a 10-second cache of shop rows.
"""

import asyncio
import json
import logging
import re
import threading
import time
import unicodedata
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

import psycopg
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_server import agent
from agent_server.auth import MAX_TOKEN_LIFETIME, verify_customer_claims
from agent_server.commerce import adapter_for, close_clients
from agent_server.rate_limit import ChatLimiter, DbChatLimiter
from agent_server.store import (advisory_lock, append_turn, claim_conversation, close_ticket,
                                conversation_owned, create_conversation, delete_empty_conversation,
                                demo_shop_enabled, feedback_counts, guest_allowed, init_db,
                                list_conversations, list_faqs, list_messages, list_offers, list_tickets,
                                origin_of, release_conversation, replace_faqs, replace_offers,
                                revoke_customer_tokens, save_feedback, save_trace, shop_by_public_key,
                                shop_for_write_key, token_revoked, turns_since)
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
    await close_clients()  # the connection pools to the shops' APIs


class BodyTooLarge(Exception):
    pass


class BodyLimit:
    """Refuse request bodies over the limit before they are read whole (chat: 64 KB; content
    pushes under /v1/shops/: 4 MB). A body is otherwise parsed before any key or origin check."""

    def __init__(self, app, default: int = 64_000, content: int = 4_000_000) -> None:
        self.app, self.default, self.content = app, default, content

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self.content if scope["path"].startswith("/v1/shops/") else self.default
        length = dict(scope.get("headers") or []).get(b"content-length", b"")
        if length.isdigit() and int(length) > limit:
            return await self.reject(send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except BodyTooLarge:
            await self.reject(send)

    @staticmethod
    async def reject(send) -> None:
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"detail":"request_too_large"}'})


app = FastAPI(title="ATI Agent Server", version="1.0.0", lifespan=lifespan)
app.add_middleware(BodyLimit)
llm = LLMProvider()


@app.exception_handler(RequestValidationError)
async def invalid_request(_: Request, error: RequestValidationError):
    """422 without echoing the input back (it can be large or carry personal data)."""
    return JSONResponse({"detail": [{"loc": item.get("loc"), "msg": item.get("msg")}
                                    for item in error.errors()[:5]]}, status_code=422)


@app.exception_handler(psycopg.DataError)
async def invalid_data(_: Request, error: psycopg.DataError):
    """Values PostgreSQL cannot store (a NUL byte in an identifier) are a bad request, not a crash."""
    return JSONResponse({"detail": "invalid_characters"}, status_code=400)


def turns_today(shop_id: str) -> int:
    midnight = datetime.combine(datetime.now().date(), datetime.min.time()).astimezone()
    return turns_since(shop_id, midnight)


def make_chat_limiter() -> ChatLimiter | DbChatLimiter:
    limits = {
        "per_customer_per_minute": env_int("RATE_LIMIT_PER_SESSION_PER_MINUTE", 10),
        "per_ip_per_minute": env_int("RATE_LIMIT_PER_IP_PER_MINUTE", 30),
        "daily_per_shop": env_int("DAILY_CHAT_QUOTA_PER_SHOP", 500),
        # One sender must not be able to use up the whole shop's day.
        "daily_per_customer": env_int("DAILY_CHAT_QUOTA_PER_CUSTOMER", 100),
        "daily_per_ip": env_int("DAILY_CHAT_QUOTA_PER_IP", 300),
    }
    if env("RATE_LIMIT_BACKEND", "db") == "memory":  # a single process that keeps no rate table
        return ChatLimiter(**limits, daily_used=turns_today)
    return DbChatLimiter(**limits)


chat_limiter = make_chat_limiter()


class ChatRequest(BaseModel):
    # customer_id must never be accepted from the browser.
    model_config = ConfigDict(extra="forbid")
    shop_key: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    conversation_id: str | None = Field(default=None, max_length=100)

    @field_validator("message")
    @classmethod
    def printable(cls, value: str) -> str:
        """Composed Unicode (Unikey "tổ hợp", macOS paste) and no control characters (Postgres
        rejects NUL, after a whole answer was streamed)."""
        value = unicodedata.normalize("NFC", value)
        return "".join(char for char in value if char in "\n\t" or ord(char) >= 32)


class LogoutRequest(BaseModel):
    shop_key: str = Field(min_length=1, max_length=100)


class ShopLogout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: str = Field(min_length=1, max_length=200)
    logout_at: int | None = None  # epoch seconds on the shop's clock, the clock that sets `iat`


class FeedbackRequest(BaseModel):
    shop_key: str = Field(min_length=1, max_length=100)
    conversation_id: str = Field(min_length=1, max_length=100)
    rating: Literal["yes", "no"]


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


# ---------------------------------------------------------------- who is calling

SHOP_CACHE_SECONDS = 10.0
_shop_cache: dict[str, tuple[float, dict | None]] = {}


def clear_shop_cache() -> None:
    _shop_cache.clear()


async def require_shop_key(shop_key: str) -> dict:
    """The shop row, cached for a few seconds: every widget request needs it, and it rarely changes
    (a change made through another process is seen here within SHOP_CACHE_SECONDS)."""
    hit = _shop_cache.get(shop_key)
    if hit and time.monotonic() - hit[0] < SHOP_CACHE_SECONDS:
        shop = hit[1]
    else:
        shop = await asyncio.to_thread(shop_by_public_key, shop_key)
        if len(_shop_cache) > 1000:
            _shop_cache.clear()
        _shop_cache[shop_key] = (time.monotonic(), shop)
    if not shop:
        raise HTTPException(404, "unknown_shop_key")
    return shop


async def writable_shop(shop_id: str, authorization: str | None) -> dict:
    """The shop whose write key the shop's backend or staff presented (content, tickets, logout)."""
    scheme, _, key = (authorization or "").partition(" ")
    shop = (await asyncio.to_thread(shop_for_write_key, shop_id, key)
            if scheme.lower() == "bearer" and key else None)
    if not shop:
        raise HTTPException(401, "invalid_faq_write_key", headers={"WWW-Authenticate": "Bearer"})
    return shop


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


# ---------------------------------------------------------------- widget files

@app.get("/widget/frame")
async def frame(shop_key: str = Query(...), parent_origin: str = Query(...)):
    shop = await require_shop_key(shop_key)
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


# The static files of the widget; nothing else in widget/ is served.
WIDGET_FILES = {"loader.js": "text/javascript", "loader.css": "text/css",
                "frame.js": "text/javascript", "frame-core.js": "text/javascript", "frame.css": "text/css"}


@app.get("/widget/{name}")
async def widget_file(name: str):
    media_type = WIDGET_FILES.get(name)
    if not media_type:
        raise HTTPException(404, "not_found")
    return FileResponse(WIDGET_DIR / name, media_type=media_type, headers={"Cache-Control": "no-cache"})


@app.get("/v1/widget-config")
async def widget_config(shop_key: str):
    shop = await require_shop_key(shop_key)
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


# ---------------------------------------------------------------- content pushed by the shop

def checked_snapshot(shop: dict, items: list[dict], kind: str, text_fields: tuple[str, ...]) -> list[dict]:
    """Checks shared by FAQ and offer pushes: unique ids, no text that is blank once trimmed, and
    links only to the shop's own site."""
    if len({item["id"] for item in items}) != len(items):
        raise HTTPException(422, f"duplicate_{kind}_id")
    expected_origin = origin_of(shop["shop_url"])
    for item in items:
        for field in text_fields:
            item[field] = item[field].strip()
            if not item[field]:
                raise HTTPException(422, f"empty_{kind}_field")
        if item["source_url"] and origin_of(item["source_url"]) != expected_origin:
            raise HTTPException(422, "source_url_must_match_shop_origin")
    return items


def store_faqs(shop: dict, faqs: list[dict]) -> tuple[int, int, bool]:
    """Save and index a FAQ snapshot (in a worker thread). One push at a time per shop, in every
    server process, and the index is built from what the table now holds, so two overlapping
    pushes cannot leave the search index on the older snapshot."""
    from rag.ingest import index_faqs_sync
    with advisory_lock(f"faq:{shop['shop_id']}"):
        count, revision = replace_faqs(shop["shop_id"], faqs)
        try:
            index_faqs_sync(shop["shop_id"], shop["shop_url"], list_faqs(shop["shop_id"]))
            indexed = True
        except Exception:
            logger.exception("FAQ indexing failed for shop %s", shop["shop_id"])
            indexed = False
    return count, revision, indexed


@app.put("/v1/shops/{shop_id}/faqs")
async def sync_faqs(shop_id: str, payload: FaqSnapshot,
                    authorization: str | None = Header(default=None)):
    """A customer's backend pushes its complete FAQ snapshot; it is indexed for search."""
    shop = await writable_shop(shop_id, authorization)
    faqs = checked_snapshot(shop, [faq.model_dump() for faq in payload.faqs], "faq", ("question", "answer"))
    for faq in faqs:
        faq["keywords"] = [keyword.strip() for keyword in faq["keywords"]]
        if any(not keyword or len(keyword) > 120 for keyword in faq["keywords"]):
            raise HTTPException(422, "invalid_keyword")
    count, revision, indexed = await asyncio.to_thread(store_faqs, shop, faqs)
    return {"shop_id": shop_id, "count": count, "revision": revision, "indexed": indexed}


@app.get("/v1/shops/{shop_id}/faqs")
async def get_faqs(shop_id: str, authorization: str | None = Header(default=None)):
    await writable_shop(shop_id, authorization)
    return {"shop_id": shop_id, "faqs": await asyncio.to_thread(list_faqs, shop_id)}


@app.put("/v1/shops/{shop_id}/offers")
async def sync_offers(shop_id: str, payload: OfferSnapshot,
                      authorization: str | None = Header(default=None)):
    """A customer's backend publishes promotions and dated events for this shop."""
    shop = await writable_shop(shop_id, authorization)
    offers = checked_snapshot(shop, [item.model_dump(mode="json") for item in payload.offers],
                              "offer", ("title", "description"))
    for item, original in zip(offers, payload.offers):
        start, end = original.starts_at, original.ends_at
        if item["kind"] == "event" and (start is None or end is None):
            raise HTTPException(422, "event_dates_required")
        if (start is None) != (end is None):
            raise HTTPException(422, "both_offer_dates_required")
        if start and (not start.tzinfo or not end.tzinfo or end <= start):
            raise HTTPException(422, "invalid_offer_dates")
    count, revision = await asyncio.to_thread(replace_offers, shop_id, offers)
    return {"shop_id": shop_id, "count": count, "revision": revision}


@app.get("/v1/shops/{shop_id}/offers")
async def get_offers(shop_id: str, authorization: str | None = Header(default=None)):
    await writable_shop(shop_id, authorization)
    return {"shop_id": shop_id, **await asyncio.to_thread(list_offers, shop_id)}


@app.get("/v1/shops/{shop_id}/tickets")
async def get_tickets(shop_id: str, authorization: str | None = Header(default=None),
                      status: Literal["open", "closed"] | None = None,
                      before: int | None = Query(default=None, ge=1)):
    """Handover tickets for the shop's staff (same write key as content pushes), newest first;
    `before` = the smallest id of the previous page."""
    await writable_shop(shop_id, authorization)
    tickets = await asyncio.to_thread(list_tickets, shop_id, status, before)
    return {"shop_id": shop_id, "tickets": tickets,
            "next_before": tickets[-1]["id"] if len(tickets) == 100 else None}


@app.post("/v1/shops/{shop_id}/tickets/{ticket_id}/close")
async def close_shop_ticket(shop_id: str, ticket_id: int, authorization: str | None = Header(default=None)):
    await writable_shop(shop_id, authorization)
    if not await asyncio.to_thread(close_ticket, shop_id, ticket_id):
        raise HTTPException(404, "unknown_ticket")
    return {"ok": True}


@app.get("/v1/shops/{shop_id}/feedback")
async def get_feedback(shop_id: str, authorization: str | None = Header(default=None)):
    """👍/👎 counts for the shop's staff (one rating per conversation)."""
    await writable_shop(shop_id, authorization)
    return {"shop_id": shop_id, **await asyncio.to_thread(feedback_counts, shop_id)}


# ---------------------------------------------------------------- chat

TRACE_ARG_LIMIT = 80


def trace_tools(calls: list[dict]) -> list[dict]:
    """Tool names, success and masked short arguments: never outputs or free-text summaries."""
    safe = []
    for call in calls:
        args = {key: mask_text(str(value))[:TRACE_ARG_LIMIT] for key, value in call["args"].items()
                if key != "transcript_summary"}
        safe.append({"name": call["name"], "args": args, "ok": call["ok"]})
    return safe


def turn_trace(done: dict, message_id: int) -> dict:
    """The trace row of an answered turn (no message text)."""
    trace = {"message_id": message_id, "mode": done["mode"], "model": done.get("model"),
             "llm_calls": done["llm_calls"], "tools_called": trace_tools(done["tools"]),
             "latency_first_token_ms": done.get("first_token_ms"),
             **{key: done["usage"][key] for key in ("input_tokens", "output_tokens", "cached_tokens")}}
    if done.get("fallback_reason"):  # Why the model was not used, for operators.
        trace["error"] = f"fallback: {done['fallback_reason']}"[:300]
    return trace


def turn_context(shop: dict, customer_id: str, conversation_id: str) -> TurnContext:
    return TurnContext(shop=shop, customer_id=customer_id, conversation_id=conversation_id,
                       turn_key=f"{conversation_id}:{uuid.uuid4().hex}", adapter=adapter_for(shop),
                       search=search)


async def wait_for_conversation(conversation_id: str) -> None:
    """Take the conversation for this turn once its earlier turn (in any server process) is saved.
    The claim lapses by itself after TURN_TIMEOUT + 30 s, should a process die mid-turn."""
    deadline, delay = time.monotonic() + TURN_TIMEOUT, 0.05
    while not await asyncio.to_thread(claim_conversation, conversation_id, TURN_TIMEOUT + 30):
        if time.monotonic() > deadline:
            raise TimeoutError("the conversation's previous turn did not finish")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 0.5)


async def release(conversation_id: str) -> None:
    """Let the next turn start. Never fails the turn: an unreleased claim lapses by itself."""
    try:
        await asyncio.to_thread(release_conversation, conversation_id)
    except Exception:
        logger.exception("conversation not released (it lapses by itself)")


async def enforce_chat_limits(shop: dict, customer_id: str, request: Request) -> None:
    limited = await asyncio.to_thread(chat_limiter.check, shop["shop_id"], customer_id,
                                      request.client.host if request.client else "unknown",
                                      guest=customer_id.startswith("guest:"))
    if limited:
        reason, retry_after = limited
        raise HTTPException(429, reason, headers={"Retry-After": str(retry_after)})


async def chat_events(shop: dict, customer_id: str, conversation_id: str, message: str, new: bool):
    """One turn as Server-Sent Events (contract C4): status/token/replace/sources ..., then done or error."""
    started = time.perf_counter()
    first_ms = None
    trace = {"conversation_id": conversation_id, "shop_id": shop["shop_id"], "mode": "error"}
    created = claimed = False
    try:
        if new:
            await asyncio.to_thread(create_conversation, conversation_id, shop["shop_id"], customer_id, message)
            created = True
        await wait_for_conversation(conversation_id)
        claimed = True
        turn_started = time.perf_counter()  # waiting for an earlier turn does not count
        history = [] if new else (await asyncio.to_thread(
            list_messages, conversation_id, shop["shop_id"], customer_id, 8) or [])
        done = None
        async for event in agent.run(turn_context(shop, customer_id, conversation_id), history, message, llm=llm):
            if time.perf_counter() - turn_started > TURN_TIMEOUT:
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
        trace.update(turn_trace(done, message_id), latency_first_ms=first_ms,
                     latency_total_ms=round((time.perf_counter() - started) * 1000))
        await release(conversation_id)  # the next turn may start while this one's trace is written
        claimed = False
        # The customer gets "done" now; the trace is written after it.
        yield sse("done", {"conversation_id": conversation_id, "mode": done["mode"]})
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
    finally:
        if claimed:
            await release(conversation_id)
        trace.setdefault("latency_first_ms", first_ms)
        trace.setdefault("latency_total_ms", round((time.perf_counter() - started) * 1000))
        try:
            await asyncio.to_thread(save_trace, trace)
        except Exception:
            logger.exception("trace not saved")


@app.post("/v1/chat")
async def chat(payload: ChatRequest, request: Request,
               authorization: str | None = Header(default=None),
               x_ati_guest: str | None = Header(default=None)):
    require_widget_origin(request)
    shop = await require_shop_key(payload.shop_key)
    customer_id = await asyncio.to_thread(require_customer, shop, authorization, x_ati_guest)
    message = payload.message.strip()
    if not message:
        raise HTTPException(400, "empty_message")
    conversation_id = payload.conversation_id or uuid.uuid4().hex
    if payload.conversation_id and not await asyncio.to_thread(
            conversation_owned, conversation_id, shop["shop_id"], customer_id):
        raise HTTPException(404, "unknown_conversation")
    await enforce_chat_limits(shop, customer_id, request)
    return StreamingResponse(
        chat_events(shop, customer_id, conversation_id, message, new=not payload.conversation_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"})


@app.get("/v1/conversations")
async def conversations(shop_key: str, authorization: str | None = Header(default=None),
                        x_ati_guest: str | None = Header(default=None)):
    shop = await require_shop_key(shop_key)
    customer_id = await asyncio.to_thread(require_customer, shop, authorization, x_ati_guest)
    rows = await asyncio.to_thread(list_conversations, shop["shop_id"], customer_id)
    return JSONResponse({"conversations": rows}, headers={"Cache-Control": "no-store"})


@app.get("/v1/conversations/{conversation_id}")
async def conversation(conversation_id: str, shop_key: str,
                       authorization: str | None = Header(default=None),
                       x_ati_guest: str | None = Header(default=None)):
    shop = await require_shop_key(shop_key)
    customer_id = await asyncio.to_thread(require_customer, shop, authorization, x_ati_guest)
    messages = await asyncio.to_thread(list_messages, conversation_id, shop["shop_id"], customer_id)
    if messages is None:
        raise HTTPException(404, "unknown_conversation")
    return JSONResponse({"conversation_id": conversation_id, "messages": messages},
                        headers={"Cache-Control": "no-store"})


@app.post("/v1/logout")
async def logout(payload: LogoutRequest, request: Request,
                 authorization: str | None = Header(default=None)):
    """The widget, when the shop page calls ATI.reset() on logout: the token it holds, and every
    earlier token of that customer, stop working at once. An expired token is accepted here (it
    only removes rights), and its own `iat` is the cut-off, so the shop's clock decides, not ours."""
    require_widget_origin(request)
    shop = await require_shop_key(payload.shop_key)
    scheme, _, token = (authorization or "").partition(" ")
    claims = (verify_customer_claims(token, shop, expired_grace=MAX_TOKEN_LIFETIME)
              if scheme.lower() == "bearer" and token else None)
    if not claims:
        raise HTTPException(401, "invalid_chat_token", headers={"WWW-Authenticate": "Bearer"})
    await asyncio.to_thread(revoke_customer_tokens, shop["shop_id"], claims["sub"], claims["iat"])
    return {"ok": True}


@app.post("/v1/shops/{shop_id}/logout")
async def shop_logout(shop_id: str, payload: ShopLogout, authorization: str | None = Header(default=None)):
    """The shop's backend, when a customer logs out (sdk: revoke_user_tokens): every chat token issued
    to that customer until now stops working, on every device."""
    await writable_shop(shop_id, authorization)
    now = int(time.time())
    logout_at = min(payload.logout_at, now + 300) if payload.logout_at else now
    await asyncio.to_thread(revoke_customer_tokens, shop_id, payload.customer_id, logout_at)
    return {"ok": True}


@app.post("/v1/feedback")
async def feedback(payload: FeedbackRequest, request: Request,
                   authorization: str | None = Header(default=None),
                   x_ati_guest: str | None = Header(default=None)):
    require_widget_origin(request)
    shop = await require_shop_key(payload.shop_key)
    customer_id = await asyncio.to_thread(require_customer, shop, authorization, x_ati_guest)
    if not await asyncio.to_thread(conversation_owned, payload.conversation_id, shop["shop_id"], customer_id):
        raise HTTPException(404, "unknown_conversation")
    await asyncio.to_thread(save_feedback, payload.conversation_id, shop["shop_id"], payload.rating)
    return {"ok": True}


@app.get("/v1/demo-stats")
async def demo_stats():
    """Public: only whether the model is in use. Feedback counts are per shop, behind its key."""
    return {"mode": mode()}
