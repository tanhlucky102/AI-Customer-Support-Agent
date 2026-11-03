"""Test setup: a separate `ati_test` database, a bag-of-words embedder, no reranker and no LLM.

Import this module before any project module so the environment is set first:
    python -m unittest discover -s tests -t .
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

# The bag-of-words test embedder gives lower cosine scores than BGE-M3, hence the lower τ.
os.environ.update({"DEMO_SHOP": "1", "RAG_EMBEDDER": "hash", "RAG_RERANK": "0", "RAG_WARMUP": "0",
                   "RAG_TAU_NO_RERANK": "0.15", "AI_BOX_API_KEY": "",
                   "DAILY_CHAT_QUOTA_PER_SHOP": "500"})

import psycopg  # noqa: E402

from shared import db  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_ready = False


def use_test_database() -> None:
    global _ready
    if _ready:
        return
    url = os.environ.get("ATI_TEST_DATABASE_URL")
    if not url:
        base = os.environ.get("DATABASE_URL") or db._start_embedded()
        with psycopg.connect(base, autocommit=True) as connection:
            if not connection.execute("SELECT 1 FROM pg_database WHERE datname = 'ati_test'").fetchone():
                connection.execute("CREATE DATABASE ati_test")
        url = base.rsplit("/", 1)[0] + "/ati_test"
    os.environ["DATABASE_URL"] = url
    db.close_pool()
    _ready = True


def reset_database() -> None:
    use_test_database()
    db.init_schema()
    with db.connect() as connection:
        connection.execute("TRUNCATE shops, kb_context_cache RESTART IDENTITY CASCADE")
    from rag import bm25
    bm25._cache.clear()
    from agent_server.store import init_db
    init_db()


def ingest_kb(shop_id: str, shop_url: str) -> dict:
    from rag.ingest import ingest_dir
    return asyncio.run(ingest_dir(shop_id, shop_url, ROOT / "data" / "kb" / shop_id, contextual=False))


def encoded(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()


def token(customer_id: str, secret: str, expires_in: int = 600, shop_id: str = "demo_shop") -> str:
    now = int(time.time())
    unsigned = encoded({"alg": "HS256", "typ": "JWT"}) + "." + encoded({
        "iss": shop_id, "sub": customer_id, "aud": "ati-agent", "iat": now, "exp": now + expires_in})
    signature = hmac.new(secret.encode(), unsigned.encode(), hashlib.sha256).digest()
    return unsigned + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()


def sse_events(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.split("\n\n"):
        lines = block.splitlines()
        if len(lines) >= 2 and lines[0].startswith("event: "):
            events.append((lines[0][7:], json.loads(lines[1].removeprefix("data: "))))
    return events


def reply_text(text: str) -> str:
    reply = ""
    for name, data in sse_events(text):
        if name == "token":
            reply += data["text"]
        elif name == "replace":
            reply = data["text"]
    return reply


class FakeLLM:
    """Scripted stand-in for LLMProvider. Each step is a list of (tool, args) or a reply string."""

    model = "fake-llm"
    enabled = True

    def __init__(self, *steps, fail: Exception | None = None):
        self.steps = list(steps)
        self.fail = fail
        self.requests: list[dict] = []

    async def stream(self, messages, tools=None, tool_choice="auto"):
        from shared.llm import LLMResult, Usage
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools,
                              "tool_choice": tool_choice})
        if self.fail:
            raise self.fail
        step = self.steps.pop(0) if self.steps else "OK"
        result = LLMResult(usage=Usage(input_tokens=100, output_tokens=10, cached_tokens=60, calls=1))
        if isinstance(step, str):
            result.content = step
            result.finish_reason = "stop"
            for start in range(0, len(step), 8):
                yield {"type": "token", "text": step[start:start + 8]}
        else:
            if isinstance(step, tuple):  # (text written before the calls, calls)
                result.content, step = step
                yield {"type": "token", "text": result.content}
            result.finish_reason = "tool_calls"
            result.tool_calls = [{"id": f"call_{len(self.requests)}_{index}", "name": name,
                                  "arguments": json.dumps(args)} for index, (name, args) in enumerate(step)]
        yield {"type": "result", "result": result}
