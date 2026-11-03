"""One PostgreSQL + pgvector database for the framework: shop registry, chat history, tickets,
traces and the knowledge base.

DATABASE_URL selects the server (Docker Compose sets it). When it is empty, an embedded PostgreSQL
with pgvector (the `pgserver` package) is started in data/pgdata, so the project also runs on a
laptop without Docker or admin rights.
"""

import os
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from shared.settings import ROOT, env

EMBEDDING_DIM = 1024

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS shops (
    shop_id TEXT PRIMARY KEY,
    public_key TEXT NOT NULL UNIQUE,
    faq_key_digest TEXT NOT NULL,
    title TEXT NOT NULL,
    greeting TEXT NOT NULL,
    primary_color TEXT NOT NULL,
    shop_url TEXT NOT NULL,
    allowed_origins JSONB NOT NULL,
    suggestions JSONB NOT NULL,
    chat_token_secret TEXT,
    tone TEXT NOT NULL DEFAULT '',
    commerce_config JSONB,
    commerce_api_key TEXT,
    faq_revision INTEGER NOT NULL DEFAULT 0,
    faq_updated_at TIMESTAMPTZ,
    offers_revision INTEGER NOT NULL DEFAULT 0,
    offers_updated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Level-0 shops without customer accounts may let visitors chat without logging in (guest mode).
ALTER TABLE shops ADD COLUMN IF NOT EXISTS guest_chat BOOLEAN NOT NULL DEFAULT false;
-- What the shop promises after a handover ("Nhân viên phản hồi qua email trong 24 giờ"); empty = no time promised.
ALTER TABLE shops ADD COLUMN IF NOT EXISTS handover_note TEXT NOT NULL DEFAULT '';
ALTER TABLE shops ADD COLUMN IF NOT EXISTS handover_note_en TEXT NOT NULL DEFAULT '';

-- Logging out cuts off the customer's chat tokens: a token with iat <= logout_at is rejected.
CREATE TABLE IF NOT EXISTS token_cutoffs (
    shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
    customer_id TEXT NOT NULL,
    logout_at BIGINT NOT NULL,
    PRIMARY KEY (shop_id, customer_id)
);

CREATE TABLE IF NOT EXISTS faqs (
    shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
    faq_id TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    keywords JSONB NOT NULL,
    source_url TEXT,
    PRIMARY KEY (shop_id, faq_id)
);

CREATE TABLE IF NOT EXISTS offers (
    shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
    offer_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('promotion', 'event')),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    starts_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    source_url TEXT,
    PRIMARY KEY (shop_id, offer_id)
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
    customer_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'escalated', 'closed')),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_owner
    ON conversations (shop_id, customer_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    sources JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages (conversation_id, id);

CREATE TABLE IF NOT EXISTS tickets (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
    customer_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tickets_shop_status ON tickets (shop_id, status, id);

-- One rating per conversation (the latest), so repeated clicks cannot inflate the counts.
CREATE TABLE IF NOT EXISTS feedback (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
    rating TEXT NOT NULL CHECK (rating IN ('yes', 'no')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS traces (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT REFERENCES messages(id) ON DELETE CASCADE,
    conversation_id TEXT,
    shop_id TEXT NOT NULL,
    model TEXT,
    mode TEXT NOT NULL,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    tools_called JSONB NOT NULL DEFAULT '[]',
    latency_first_ms INTEGER,
    latency_first_token_ms INTEGER,
    latency_total_ms INTEGER,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_documents (
    id BIGSERIAL PRIMARY KEY,
    shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    source_url TEXT,
    origin TEXT NOT NULL DEFAULT 'file' CHECK (origin IN ('file', 'faq')),
    updated_at DATE,
    content_hash TEXT NOT NULL,
    UNIQUE (shop_id, slug)
);

CREATE TABLE IF NOT EXISTS kb_sections (
    id TEXT PRIMARY KEY,
    doc_id BIGINT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    shop_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    heading TEXT NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id TEXT PRIMARY KEY,
    doc_id BIGINT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    section_id TEXT NOT NULL REFERENCES kb_sections(id) ON DELETE CASCADE,
    shop_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    content TEXT NOT NULL,
    context_prefix TEXT NOT NULL DEFAULT '',
    token_count INTEGER NOT NULL,
    embedding vector({EMBEDDING_DIM}),
    content_tsv tsvector,
    embedding_model TEXT
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_shop ON kb_chunks (shop_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_embedding ON kb_chunks
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_tsv ON kb_chunks USING gin (content_tsv);

CREATE TABLE IF NOT EXISTS kb_context_cache (
    hash TEXT PRIMARY KEY,
    context TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_pool: ConnectionPool | None = None
_lock = threading.Lock()
_embedded = None  # Keeps the pgserver handle alive for the life of the process.


def _ascii(path: Path) -> bool:
    return str(path).isascii()


def _embedded_home() -> Path:
    """Postgres binaries on Windows cannot run from, or store data in, a non-ASCII path
    (e.g. a folder named 'Kì 1 Năm 4'), so they live under the user's local app data then."""
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ati-pgserver"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _start_embedded() -> str:
    import pgserver  # Imported lazily: deployments with DATABASE_URL do not need it.
    from pgserver import _commands, postgres_server

    if not _ascii(_commands.POSTGRES_BIN_PATH):
        install = _embedded_home() / f"pginstall-{pgserver.__version__ if hasattr(pgserver, '__version__') else 'bundled'}"
        if not (install / "bin").exists():
            shutil.copytree(_commands.POSTGRES_BIN_PATH.parent, install, dirs_exist_ok=True)
        _commands.POSTGRES_BIN_PATH = install / "bin"
        postgres_server.POSTGRES_BIN_PATH = install / "bin"
    pgdata = Path(env("PGDATA_DIR") or ROOT / "data" / "pgdata")
    if not _ascii(pgdata):
        pgdata = _embedded_home() / "pgdata"
    pgdata.mkdir(parents=True, exist_ok=True)
    global _embedded
    for attempt in range(6):
        try:
            _embedded = pgserver.get_server(pgdata, cleanup_mode=None)
            return _embedded.get_uri()
        except (subprocess.TimeoutExpired, AssertionError):
            # After an unclean shutdown Postgres first syncs and replays its WAL; on Windows that
            # takes ~40 s, longer than pgserver's 10 s wait. The server keeps starting, but pgserver
            # caches the half-started handle (get_uri then fails an assertion), so drop it and ask
            # again: the next handle finds the running server through postmaster.pid.
            postgres_server.PostgresServer._instances.pop(pgdata.expanduser().resolve(), None)
            if attempt == 5:
                raise
            time.sleep(10)


def database_url() -> str:
    url = env("DATABASE_URL")
    if url:
        # Older .env files used the SQLAlchemy spelling.
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    return _start_embedded()


def _configure(connection: psycopg.Connection) -> None:
    register_vector(connection)


def pool() -> ConnectionPool:
    global _pool
    with _lock:
        if _pool is None:
            url = database_url()
            # The vector type must exist before connections can register it.
            with psycopg.connect(url, autocommit=True) as connection:
                connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            # Fail within 5 s when the database is unreachable, instead of psycopg_pool's 30 s.
            _pool = ConnectionPool(url, min_size=1, max_size=8, open=True, configure=_configure,
                                   kwargs={"row_factory": dict_row}, timeout=5)
        return _pool


@contextmanager
def connect():
    """A pooled connection whose transaction commits on success and rolls back on error."""
    with pool().connection() as connection:
        yield connection


def init_schema() -> None:
    with connect() as connection:
        connection.execute(SCHEMA)


def close_pool() -> None:
    """Used by tests that switch DATABASE_URL between runs."""
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None
