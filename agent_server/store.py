"""Agent Server data in PostgreSQL: shop registry, pushed FAQ/offers, chat history, tickets, traces."""

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

from shared.db import connect, init_schema
from shared.settings import env, env_flag

DEMO_DATA = json.loads((Path(__file__).parent / "demo_data.json").read_text(encoding="utf-8"))
DEMO_SHOP_URL = env("DEMO_SHOP_URL", "http://localhost:8001").rstrip("/")
# With no configured key, use an undisclosed process-local value: demo chat tokens are rejected
# and demo FAQ writes are disabled instead of accepting a key published in the repository.
DEMO_TOKEN_SECRET = env("DEMO_SHOP_TOKEN_SECRET") or secrets.token_urlsafe(32)
DEMO_FAQ_WRITE_KEY = env("DEMO_FAQ_WRITE_KEY") or secrets.token_urlsafe(32)


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def key_digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def demo_commerce_config() -> dict:
    """The demo shop implements contract C2, so it needs only the standard profile."""
    return {"profile": "standard",
            "base_url": env("DEMO_SHOP_API_URL", DEMO_SHOP_URL).rstrip("/")}


def demo_shop_enabled() -> bool:
    """Create the Demo Mobile shop at start-up only on a machine set up for the demo (its keys are in
    .env, as scripts/setup_demo.py writes them) or when DEMO_SHOP=1. A real deployment has neither, so
    it serves only the shops created with provision_shop."""
    return env_flag("DEMO_SHOP", bool(env("DEMO_SHOP_TOKEN_SECRET")))


def init_db() -> None:
    init_schema()
    if not demo_shop_enabled():
        return
    origins = ["http://localhost:8001", "http://127.0.0.1:8001",
               "http://localhost:8002", "http://127.0.0.1:8002"]
    origins.extend(item.strip() for item in env("WIDGET_ALLOWED_ORIGINS").split(",") if item.strip())
    origins.append(origin_of(DEMO_SHOP_URL))
    origins = sorted({origin_of(item) for item in origins if origin_of(item)})
    with connect() as db:
        db.execute("""INSERT INTO shops
            (shop_id, public_key, faq_key_digest, title, greeting, primary_color, shop_url,
             allowed_origins, suggestions, chat_token_secret, tone, commerce_config, commerce_api_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (shop_id) DO UPDATE SET
                faq_key_digest = EXCLUDED.faq_key_digest, shop_url = EXCLUDED.shop_url,
                allowed_origins = EXCLUDED.allowed_origins, suggestions = EXCLUDED.suggestions,
                chat_token_secret = EXCLUDED.chat_token_secret,
                commerce_config = EXCLUDED.commerce_config,
                commerce_api_key = EXCLUDED.commerce_api_key""", (
            DEMO_DATA["shop_id"], DEMO_DATA["public_key"], key_digest(DEMO_FAQ_WRITE_KEY),
            DEMO_DATA["title"], DEMO_DATA["greeting"], DEMO_DATA["primary_color"], DEMO_SHOP_URL,
            Jsonb(origins), Jsonb(DEMO_DATA["suggestions"]), DEMO_TOKEN_SECRET,
            DEMO_DATA.get("tone", ""), Jsonb(demo_commerce_config()), env("DEMO_SHOP_API_KEY"),
        ))


def shop_by_public_key(public_key: str) -> dict | None:
    with connect() as db:
        return db.execute("SELECT * FROM shops WHERE public_key = %s", (public_key,)).fetchone()


def shop_by_id(shop_id: str) -> dict | None:
    with connect() as db:
        return db.execute("SELECT * FROM shops WHERE shop_id = %s", (shop_id,)).fetchone()


def verify_faq_key(shop_id: str, key: str) -> bool:
    shop = shop_by_id(shop_id)
    expected = shop["faq_key_digest"] if shop else "0" * 64
    return bool(shop) and hmac.compare_digest(key_digest(key), expected)


def create_shop(*, shop_id: str, public_key: str, faq_write_key: str, title: str,
                greeting: str, primary_color: str, shop_url: str,
                allowed_origins: list[str], chat_token_secret: str,
                suggestions: list[str] | None = None, tone: str = "",
                commerce_config: dict | None = None, commerce_api_key: str | None = None,
                guest_chat: bool = False) -> None:
    with connect() as db:
        db.execute("""INSERT INTO shops
            (shop_id, public_key, faq_key_digest, title, greeting, primary_color, shop_url,
             allowed_origins, suggestions, chat_token_secret, tone, commerce_config, commerce_api_key,
             guest_chat)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", (
            shop_id, public_key, key_digest(faq_write_key), title, greeting, primary_color,
            shop_url.rstrip("/"), Jsonb(sorted(set(allowed_origins))), Jsonb(suggestions or []),
            chat_token_secret, tone, Jsonb(commerce_config) if commerce_config else None,
            commerce_api_key, guest_chat,
        ))


def set_chat_token_secret(shop_id: str, secret: str) -> bool:
    with connect() as db:
        return bool(db.execute("UPDATE shops SET chat_token_secret = %s WHERE shop_id = %s",
                               (secret, shop_id)).rowcount)


def refresh_shop(shop_id: str, *, faq_write_key: str, chat_token_secret: str, shop_url: str,
                 allowed_origins: list[str], commerce_config: dict | None = None,
                 commerce_api_key: str | None = None, guest_chat: bool = False) -> None:
    """Bring an existing shop's keys, URL and commerce settings in line with the given values."""
    with connect() as db:
        db.execute("""UPDATE shops SET faq_key_digest = %s, chat_token_secret = %s, shop_url = %s,
                      allowed_origins = %s, commerce_config = %s, commerce_api_key = %s,
                      guest_chat = %s WHERE shop_id = %s""", (
            key_digest(faq_write_key), chat_token_secret, shop_url.rstrip("/"),
            Jsonb(sorted(set(allowed_origins))), Jsonb(commerce_config) if commerce_config else None,
            commerce_api_key, guest_chat, shop_id))


def guest_allowed(shop: dict) -> bool:
    """Guests (no login) may chat only with a shop that opted in and has no commerce API, so a
    guest can never reach order or account tools."""
    return bool(shop.get("guest_chat")) and not shop.get("commerce_config")


def set_commerce(shop_id: str, config: dict | None, api_key: str | None) -> bool:
    """Connect (or disconnect, with None) a shop's commerce API. Operator-only."""
    with connect() as db:
        return bool(db.execute(
            "UPDATE shops SET commerce_config = %s, commerce_api_key = %s WHERE shop_id = %s",
            (Jsonb(config) if config else None, api_key, shop_id)).rowcount)


def list_faqs(shop_id: str) -> list[dict]:
    with connect() as db:
        rows = db.execute("""SELECT faq_id, question, answer, keywords, source_url
                             FROM faqs WHERE shop_id = %s ORDER BY faq_id""", (shop_id,)).fetchall()
    return [{"id": row["faq_id"], "question": row["question"], "answer": row["answer"],
             "keywords": row["keywords"], "source_url": row["source_url"]} for row in rows]


def replace_faqs(shop_id: str, faqs: list[dict]) -> tuple[int, int]:
    """Replace a shop's FAQ snapshot atomically and return (count, revision)."""
    with connect() as db:
        # Lock the shop row: two concurrent pushes then run one after the other.
        db.execute("SELECT 1 FROM shops WHERE shop_id = %s FOR UPDATE", (shop_id,))
        db.execute("DELETE FROM faqs WHERE shop_id = %s", (shop_id,))
        with db.cursor() as cursor:
            cursor.executemany("""INSERT INTO faqs
                (shop_id, faq_id, question, answer, keywords, source_url)
                VALUES (%s, %s, %s, %s, %s, %s)""", [
                (shop_id, faq["id"], faq["question"], faq["answer"], Jsonb(faq["keywords"]),
                 faq.get("source_url")) for faq in faqs])
        revision = db.execute("""UPDATE shops SET faq_revision = faq_revision + 1, faq_updated_at = %s
                                 WHERE shop_id = %s RETURNING faq_revision""",
                              (now(), shop_id)).fetchone()["faq_revision"]
    return len(faqs), revision


def replace_offers(shop_id: str, offers: list[dict]) -> tuple[int, int]:
    """Replace a shop's published promotions/events snapshot atomically."""
    with connect() as db:
        db.execute("SELECT 1 FROM shops WHERE shop_id = %s FOR UPDATE", (shop_id,))
        db.execute("DELETE FROM offers WHERE shop_id = %s", (shop_id,))
        with db.cursor() as cursor:
            cursor.executemany("""INSERT INTO offers
                (shop_id, offer_id, kind, title, description, starts_at, ends_at, source_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""", [
                (shop_id, row["id"], row["kind"], row["title"], row["description"],
                 row.get("starts_at"), row.get("ends_at"), row.get("source_url")) for row in offers])
        revision = db.execute("""UPDATE shops SET offers_revision = offers_revision + 1,
                                 offers_updated_at = %s WHERE shop_id = %s RETURNING offers_revision""",
                              (now(), shop_id)).fetchone()["offers_revision"]
    return len(offers), revision


def list_offers(shop_id: str) -> dict:
    with connect() as db:
        shop = db.execute("SELECT offers_revision, offers_updated_at FROM shops WHERE shop_id = %s",
                          (shop_id,)).fetchone()
        rows = db.execute("""SELECT offer_id, kind, title, description, starts_at, ends_at, source_url
                             FROM offers WHERE shop_id = %s ORDER BY offer_id""", (shop_id,)).fetchall()
    return {"revision": shop["offers_revision"] if shop else 0,
            "updated_at": iso(shop["offers_updated_at"]) if shop else None,
            "offers": [{"id": row["offer_id"], "kind": row["kind"], "title": row["title"],
                        "description": row["description"], "starts_at": iso(row["starts_at"]),
                        "ends_at": iso(row["ends_at"]), "source_url": row["source_url"]}
                       for row in rows]}


def conversation_owned(conversation_id: str, shop_id: str, customer_id: str) -> bool:
    with connect() as db:
        row = db.execute("SELECT 1 FROM conversations WHERE id = %s AND shop_id = %s "
                         "AND customer_id = %s", (conversation_id, shop_id, customer_id)).fetchone()
    return row is not None


def create_conversation(conversation_id: str, shop_id: str, customer_id: str,
                        first_message: str) -> None:
    title = first_message.strip().replace("\n", " ")[:80]
    with connect() as db:
        db.execute("""INSERT INTO conversations (id, shop_id, customer_id, title, created_at, updated_at)
                      VALUES (%s, %s, %s, %s, %s, %s)""",
                   (conversation_id, shop_id, customer_id, title, now(), now()))


def delete_empty_conversation(conversation_id: str) -> None:
    """Remove a conversation whose first turn failed, so it does not appear empty in history."""
    with connect() as db:
        db.execute("""DELETE FROM conversations c WHERE c.id = %s
                      AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id)""",
                   (conversation_id,))


def append_turn(conversation_id: str, shop_id: str, customer_id: str,
                question: str, reply: str, sources: list[dict]) -> int:
    """Store one question/answer pair and return the assistant message id."""
    with connect() as db:
        owned = db.execute("SELECT 1 FROM conversations WHERE id = %s AND shop_id = %s "
                           "AND customer_id = %s", (conversation_id, shop_id, customer_id)).fetchone()
        if not owned:
            raise ValueError("unknown_conversation")
        at = now()
        db.execute("""INSERT INTO messages (conversation_id, role, content, sources, created_at)
                      VALUES (%s, 'user', %s, '[]', %s)""", (conversation_id, question, at))
        message_id = db.execute("""INSERT INTO messages (conversation_id, role, content, sources,
                                   created_at) VALUES (%s, 'assistant', %s, %s, %s) RETURNING id""",
                                (conversation_id, reply, Jsonb(sources), at)).fetchone()["id"]
        db.execute("UPDATE conversations SET updated_at = %s WHERE id = %s", (at, conversation_id))
    return message_id


def list_conversations(shop_id: str, customer_id: str) -> list[dict]:
    with connect() as db:
        rows = db.execute("""SELECT id, title, status, created_at, updated_at FROM conversations
                             WHERE shop_id = %s AND customer_id = %s
                             ORDER BY updated_at DESC LIMIT 40""", (shop_id, customer_id)).fetchall()
    return [{**row, "created_at": iso(row["created_at"]), "updated_at": iso(row["updated_at"])}
            for row in rows]


def list_messages(conversation_id: str, shop_id: str, customer_id: str,
                  limit: int = 100) -> list[dict] | None:
    if not conversation_owned(conversation_id, shop_id, customer_id):
        return None
    with connect() as db:
        rows = db.execute("""SELECT role, content, sources, created_at FROM messages
                             WHERE conversation_id = %s ORDER BY id DESC LIMIT %s""",
                          (conversation_id, limit)).fetchall()
    return [{"role": row["role"], "content": row["content"], "sources": row["sources"],
             "created_at": iso(row["created_at"])} for row in reversed(rows)]


def create_ticket(*, conversation_id: str, shop_id: str, customer_id: str, reason: str,
                  summary: str, idempotency_key: str) -> str:
    """Open a support ticket once per idempotency key and return its public code."""
    with connect() as db:
        row = db.execute("""INSERT INTO tickets
            (conversation_id, shop_id, customer_id, reason, summary, idempotency_key)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO UPDATE SET idempotency_key = EXCLUDED.idempotency_key
            RETURNING id""", (conversation_id, shop_id, customer_id, reason, summary,
                              idempotency_key)).fetchone()
        db.execute("UPDATE conversations SET status = 'escalated' WHERE id = %s", (conversation_id,))
    return f"TK{row['id']:06d}"


def list_tickets(shop_id: str) -> list[dict]:
    with connect() as db:
        rows = db.execute("""SELECT id, conversation_id, customer_id, reason, summary, status,
                             created_at FROM tickets WHERE shop_id = %s ORDER BY id DESC LIMIT 100""",
                          (shop_id,)).fetchall()
    return [{**row, "code": f"TK{row['id']:06d}", "created_at": iso(row["created_at"])} for row in rows]


def save_trace(trace: dict) -> None:
    """One row per assistant turn: tokens, cache hits, tools and latency (no message text)."""
    columns = ("message_id", "conversation_id", "shop_id", "model", "mode", "llm_calls",
               "input_tokens", "output_tokens", "cached_tokens", "tools_called",
               "latency_first_ms", "latency_first_token_ms", "latency_total_ms", "error")
    values = [Jsonb(trace.get(name) or []) if name == "tools_called" else trace.get(name)
              for name in columns]
    values[columns.index("llm_calls")] = trace.get("llm_calls") or 0
    for name in ("input_tokens", "output_tokens", "cached_tokens"):
        values[columns.index(name)] = trace.get(name) or 0
    with connect() as db:
        db.execute(f"INSERT INTO traces ({', '.join(columns)}) VALUES "
                   f"({', '.join(['%s'] * len(columns))})", values)
