"""Contextual Retrieval: an LLM writes 1-2 sentences that situate each chunk in its document;
they are prepended to the chunk before embedding and keyword indexing.

Results are cached by a hash of (prompt version, model, document, chunk), so re-indexing only
pays for chunks that actually changed.
"""

import asyncio
import hashlib

from shared.db import connect
from shared.llm import LLMError, LLMProvider

PROMPT_VERSION = "ctx-v1"
PROMPT = """<document>
{document}
</document>
Đây là một đoạn trích từ tài liệu trên:
<chunk>
{chunk}
</chunk>
Viết 1-2 câu tiếng Việt ngắn gọn đặt đoạn trích này vào ngữ cảnh của toàn tài liệu (tài liệu nào,
nói về chính sách/chủ đề gì, áp dụng cho ai), để giúp tìm kiếm đoạn trích chính xác hơn.
Chỉ trả lời bằng câu ngữ cảnh, không thêm gì khác."""


def cache_key(model: str, document_text: str, chunk: str) -> str:
    return hashlib.sha256("\x1f".join([PROMPT_VERSION, model, document_text, chunk]).encode()).hexdigest()


def cached(keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    with connect() as db:
        rows = db.execute("SELECT hash, context FROM kb_context_cache WHERE hash = ANY(%s)",
                          (keys,)).fetchall()
    return {row["hash"]: row["context"] for row in rows}


def store(key: str, context: str, model: str) -> None:
    with connect() as db:
        db.execute("""INSERT INTO kb_context_cache (hash, context, model) VALUES (%s, %s, %s)
                      ON CONFLICT (hash) DO NOTHING""", (key, context, model))


async def contexts_for(document_text: str, chunks: dict[str, str], llm: LLMProvider | None = None,
                       concurrency: int = 4) -> tuple[dict[str, str], int]:
    """Return {chunk_id: context} and the number of LLM calls actually made."""
    llm = llm or LLMProvider(temperature=0)
    keys = {chunk_id: cache_key(llm.model, document_text, text) for chunk_id, text in chunks.items()}
    hits = cached(list(keys.values()))
    result = {chunk_id: hits[key] for chunk_id, key in keys.items() if key in hits}
    missing = [chunk_id for chunk_id in chunks if chunk_id not in result]
    gate = asyncio.Semaphore(concurrency)

    async def one(chunk_id: str) -> None:
        async with gate:
            prompt = PROMPT.format(document=document_text, chunk=chunks[chunk_id])
            for attempt in range(3):
                try:
                    text, _ = await llm.complete([{"role": "user", "content": prompt}], max_tokens=160)
                    break
                except LLMError:
                    if attempt == 2:
                        return  # Leave this chunk without context rather than fail the ingest.
                    await asyncio.sleep(2 * (attempt + 1))
            text = " ".join(text.split())[:400]
            if text:
                store(keys[chunk_id], text, llm.model)
                result[chunk_id] = text

    await asyncio.gather(*(one(chunk_id) for chunk_id in missing))
    return result, len(missing)
