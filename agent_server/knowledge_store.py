"""Persistent, shop-scoped FAQ storage for the widget service prototype."""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
DB_PATH = Path(os.getenv("KNOWLEDGE_DB") or Path(__file__).parent / "knowledge.db")
DEMO_DATA = json.loads((Path(__file__).parent / "demo_data.json").read_text(encoding="utf-8"))
DEMO_SHOP_URL = os.getenv("DEMO_SHOP_URL", "http://localhost:8001").rstrip("/")
DEMO_TOKEN_SECRET = os.getenv("DEMO_SHOP_TOKEN_SECRET") or "dev-only-change-me-token-secret"
# With no configured key, use an undisclosed process-local value so demo FAQ writes are disabled.
DEMO_FAQ_WRITE_KEY = os.getenv("DEMO_FAQ_WRITE_KEY") or secrets.token_urlsafe(32)


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def key_digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db() -> None:
    with closing(connect()) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS shops (
            shop_id TEXT PRIMARY KEY,
            public_key TEXT NOT NULL UNIQUE,
            faq_key_digest TEXT NOT NULL,
            title TEXT NOT NULL,
            greeting TEXT NOT NULL,
            primary_color TEXT NOT NULL,
            shop_url TEXT NOT NULL,
            allowed_origins TEXT NOT NULL,
            suggestions TEXT NOT NULL,
            chat_token_secret TEXT,
            faq_revision INTEGER NOT NULL DEFAULT 0,
            faq_updated_at TEXT,
            offers_revision INTEGER NOT NULL DEFAULT 0,
            offers_updated_at TEXT
        )""")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(shops)")}
        if "chat_token_secret" not in columns:
            db.execute("ALTER TABLE shops ADD COLUMN chat_token_secret TEXT")
        if "offers_revision" not in columns:
            db.execute("ALTER TABLE shops ADD COLUMN offers_revision INTEGER NOT NULL DEFAULT 0")
        if "offers_updated_at" not in columns:
            db.execute("ALTER TABLE shops ADD COLUMN offers_updated_at TEXT")
        db.execute("""CREATE TABLE IF NOT EXISTS faqs (
            shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
            faq_id TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            keywords TEXT NOT NULL,
            source_url TEXT,
            PRIMARY KEY (shop_id, faq_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS offers (
            shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
            offer_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('promotion', 'event')),
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            starts_at TEXT,
            ends_at TEXT,
            source_url TEXT,
            PRIMARY KEY (shop_id, offer_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
            customer_id TEXT NOT NULL,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS idx_conversations_owner
            ON conversations(shop_id, customer_id, updated_at DESC)""")
        db.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            sources TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(conversation_id, id)""")
        origins = ["http://localhost:8001", "http://127.0.0.1:8001",
                   "http://localhost:8002", "http://127.0.0.1:8002"]
        origins.extend(item.strip() for item in os.getenv("WIDGET_ALLOWED_ORIGINS", "").split(",")
                       if item.strip())
        origins.append(origin_of(DEMO_SHOP_URL))
        origins = sorted({origin_of(item) for item in origins if origin_of(item)})
        inserted = db.execute("""INSERT OR IGNORE INTO shops
            (shop_id, public_key, faq_key_digest, title, greeting, primary_color,
             shop_url, allowed_origins, suggestions, chat_token_secret)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            DEMO_DATA["shop_id"], DEMO_DATA["public_key"], key_digest(DEMO_FAQ_WRITE_KEY),
            DEMO_DATA["title"], DEMO_DATA["greeting"], DEMO_DATA["primary_color"],
            DEMO_SHOP_URL, json.dumps(origins), json.dumps(DEMO_DATA["suggestions"], ensure_ascii=False),
            DEMO_TOKEN_SECRET,
        )).rowcount
        if inserted:
            rows = [(DEMO_DATA["shop_id"], faq["slug"], faq["title"], faq["answer"],
                     json.dumps(faq["keywords"], ensure_ascii=False),
                     f"{DEMO_SHOP_URL}/policy/{faq['slug']}") for faq in DEMO_DATA["faq"]]
            db.executemany("""INSERT INTO faqs
                (shop_id, faq_id, question, answer, keywords, source_url)
                VALUES (?, ?, ?, ?, ?, ?)""", rows)
            db.execute("UPDATE shops SET faq_revision = 1, faq_updated_at = ? WHERE shop_id = ?",
                       (datetime.now(timezone.utc).isoformat(), DEMO_DATA["shop_id"]))
        else:
            # Local URL/key settings may change; never overwrite FAQ content pushed via API.
            db.execute("""UPDATE shops SET faq_key_digest = ?, shop_url = ?, allowed_origins = ?,
                          chat_token_secret = ?, suggestions = ?
                          WHERE shop_id = ?""", (
                key_digest(DEMO_FAQ_WRITE_KEY), DEMO_SHOP_URL, json.dumps(origins),
                DEMO_TOKEN_SECRET, json.dumps(DEMO_DATA["suggestions"], ensure_ascii=False),
                DEMO_DATA["shop_id"],
            ))
            current = db.execute("SELECT faq_revision FROM shops WHERE shop_id = ?",
                                 (DEMO_DATA["shop_id"],)).fetchone()
            if current["faq_revision"] == 1:
                for faq in DEMO_DATA["faq"]:
                    db.execute("UPDATE faqs SET source_url = ? WHERE shop_id = ? AND faq_id = ?",
                               (f"{DEMO_SHOP_URL}/policy/{faq['slug']}",
                                DEMO_DATA["shop_id"], faq["slug"]))


def shop_by_public_key(public_key: str) -> dict | None:
    with closing(connect()) as db:
        row = db.execute("SELECT * FROM shops WHERE public_key = ?", (public_key,)).fetchone()
        return dict(row) if row else None


def shop_by_id(shop_id: str) -> dict | None:
    with closing(connect()) as db:
        row = db.execute("SELECT * FROM shops WHERE shop_id = ?", (shop_id,)).fetchone()
        return dict(row) if row else None


def verify_faq_key(shop_id: str, key: str) -> bool:
    shop = shop_by_id(shop_id)
    expected = shop["faq_key_digest"] if shop else "0" * 64
    return bool(shop) and hmac.compare_digest(key_digest(key), expected)


def create_shop(*, shop_id: str, public_key: str, faq_write_key: str, title: str,
                greeting: str, primary_color: str, shop_url: str,
                allowed_origins: list[str], chat_token_secret: str,
                suggestions: list[str] | None = None) -> None:
    with closing(connect()) as db, db:
        db.execute("""INSERT INTO shops
            (shop_id, public_key, faq_key_digest, title, greeting, primary_color,
             shop_url, allowed_origins, suggestions, chat_token_secret)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            shop_id, public_key, key_digest(faq_write_key), title, greeting, primary_color,
            shop_url.rstrip("/"), json.dumps(sorted(set(allowed_origins))),
            json.dumps(suggestions or [], ensure_ascii=False), chat_token_secret,
        ))


def set_chat_token_secret(shop_id: str, secret: str) -> bool:
    with closing(connect()) as db, db:
        return bool(db.execute("UPDATE shops SET chat_token_secret = ? WHERE shop_id = ?",
                               (secret, shop_id)).rowcount)


def list_faqs(shop_id: str) -> list[dict]:
    with closing(connect()) as db:
        rows = db.execute("""SELECT faq_id, question, answer, keywords, source_url
                             FROM faqs WHERE shop_id = ? ORDER BY faq_id""", (shop_id,)).fetchall()
        return [{"id": row["faq_id"], "question": row["question"],
                 "answer": row["answer"], "keywords": json.loads(row["keywords"]),
                 "source_url": row["source_url"]} for row in rows]


def replace_faqs(shop_id: str, faqs: list[dict]) -> tuple[int, int]:
    """Replace a shop's FAQ snapshot atomically and return (count, revision)."""
    with closing(connect()) as db, db:
        db.execute("DELETE FROM faqs WHERE shop_id = ?", (shop_id,))
        db.executemany("""INSERT INTO faqs
            (shop_id, faq_id, question, answer, keywords, source_url)
            VALUES (?, ?, ?, ?, ?, ?)""", [
            (shop_id, faq["id"], faq["question"], faq["answer"],
             json.dumps(faq["keywords"], ensure_ascii=False), faq.get("source_url"))
            for faq in faqs
        ])
        db.execute("UPDATE shops SET faq_revision = faq_revision + 1, faq_updated_at = ? "
                   "WHERE shop_id = ?", (datetime.now(timezone.utc).isoformat(), shop_id))
        revision = db.execute("SELECT faq_revision FROM shops WHERE shop_id = ?",
                              (shop_id,)).fetchone()["faq_revision"]
        return len(faqs), revision


def replace_offers(shop_id: str, offers: list[dict]) -> tuple[int, int]:
    """Replace a shop's published promotions/events snapshot atomically."""
    with closing(connect()) as db, db:
        db.execute("DELETE FROM offers WHERE shop_id = ?", (shop_id,))
        db.executemany("""INSERT INTO offers
            (shop_id, offer_id, kind, title, description, starts_at, ends_at, source_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", [
            (shop_id, row["id"], row["kind"], row["title"], row["description"],
             row.get("starts_at"), row.get("ends_at"), row.get("source_url"))
            for row in offers
        ])
        db.execute("UPDATE shops SET offers_revision = offers_revision + 1, offers_updated_at = ? "
                   "WHERE shop_id = ?", (datetime.now(timezone.utc).isoformat(), shop_id))
        revision = db.execute("SELECT offers_revision FROM shops WHERE shop_id = ?",
                              (shop_id,)).fetchone()["offers_revision"]
        return len(offers), revision


def list_offers(shop_id: str) -> dict:
    with closing(connect()) as db:
        shop = db.execute("SELECT offers_revision, offers_updated_at FROM shops WHERE shop_id = ?",
                          (shop_id,)).fetchone()
        rows = db.execute("SELECT offer_id, kind, title, description, starts_at, ends_at, "
                          "source_url FROM offers WHERE shop_id = ? ORDER BY offer_id",
                          (shop_id,)).fetchall()
        return {"revision": shop["offers_revision"] if shop else 0,
                "updated_at": shop["offers_updated_at"] if shop else None,
                "offers": [{"id": row["offer_id"], "kind": row["kind"],
                            "title": row["title"], "description": row["description"],
                            "starts_at": row["starts_at"], "ends_at": row["ends_at"],
                            "source_url": row["source_url"]} for row in rows]}


def conversation_owned(conversation_id: str, shop_id: str, customer_id: str) -> bool:
    with closing(connect()) as db:
        row = db.execute("SELECT 1 FROM conversations WHERE id = ? AND shop_id = ? "
                         "AND customer_id = ?", (conversation_id, shop_id, customer_id)).fetchone()
        return row is not None


def create_conversation(conversation_id: str, shop_id: str, customer_id: str,
                        first_message: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    title = first_message.strip().replace("\n", " ")[:80]
    with closing(connect()) as db, db:
        db.execute("INSERT INTO conversations "
                   "(id, shop_id, customer_id, title, created_at, updated_at) "
                   "VALUES (?, ?, ?, ?, ?, ?)",
                   (conversation_id, shop_id, customer_id, title, now, now))


def append_turn(conversation_id: str, shop_id: str, customer_id: str,
                question: str, reply: str, sources: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect()) as db, db:
        owned = db.execute("SELECT 1 FROM conversations WHERE id = ? AND shop_id = ? "
                           "AND customer_id = ?", (conversation_id, shop_id, customer_id)).fetchone()
        if not owned:
            raise ValueError("unknown_conversation")
        db.executemany("INSERT INTO messages (conversation_id, role, content, sources, created_at) "
                       "VALUES (?, ?, ?, ?, ?)", [
            (conversation_id, "user", question, "[]", now),
            (conversation_id, "assistant", reply, json.dumps(sources, ensure_ascii=False), now),
        ])
        db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))


def list_conversations(shop_id: str, customer_id: str) -> list[dict]:
    with closing(connect()) as db:
        rows = db.execute("SELECT id, title, created_at, updated_at FROM conversations "
                          "WHERE shop_id = ? AND customer_id = ? ORDER BY updated_at DESC LIMIT 40",
                          (shop_id, customer_id)).fetchall()
        return [dict(row) for row in rows]


def list_messages(conversation_id: str, shop_id: str, customer_id: str,
                  limit: int = 100) -> list[dict] | None:
    if not conversation_owned(conversation_id, shop_id, customer_id):
        return None
    with closing(connect()) as db:
        rows = db.execute("SELECT role, content, sources, created_at FROM messages "
                          "WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                          (conversation_id, limit)).fetchall()
        return [{"role": row["role"], "content": row["content"],
                 "sources": json.loads(row["sources"]), "created_at": row["created_at"]}
                for row in reversed(rows)]
