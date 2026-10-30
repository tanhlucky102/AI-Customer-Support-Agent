"""Structure-aware chunking.

The parent unit is a '## ' section (what the agent reads, with its title and heading). Each section
is split into child chunks of at most `max_tokens`, cut at sentence or list-item boundaries, with
about `overlap` tokens repeated between neighbours. Children are what gets embedded and indexed;
search returns their parent section (parent-document retrieval).
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from rag.documents import Document, Section

SENTENCE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÀ-Ỹ0-9\"“(])|\n+")


@dataclass
class Chunk:
    id: str
    section_id: str
    position: int
    content: str
    token_count: int


@lru_cache(maxsize=1)
def _tokenizer():
    try:
        from transformers import AutoTokenizer
        return AutoTokenizer.from_pretrained("BAAI/bge-m3")
    except Exception:  # Offline without the model: fall back to an estimate.
        return None


def count_tokens(text: str) -> int:
    tokenizer = _tokenizer()
    if tokenizer is None:
        return max(1, round(len(text.split()) * 1.6))
    return len(tokenizer.encode(text, add_special_tokens=False))


def section_id(shop_id: str, slug: str, section: Section) -> str:
    return f"{shop_id}:{slug}#{section.position}"


def header(document: Document, section: Section) -> str:
    return f"{document.title} › {section.heading}"


def split_section(section: Section, max_tokens: int, overlap: int) -> list[str]:
    pieces = [piece.strip() for piece in SENTENCE.split(section.content) if piece and piece.strip()]
    if not pieces:
        return [section.content]
    sizes = [count_tokens(piece) for piece in pieces]
    chunks, start = [], 0
    while start < len(pieces):
        end, total = start, 0
        while end < len(pieces) and (total + sizes[end] <= max_tokens or end == start):
            total += sizes[end]
            end += 1
        chunks.append("\n".join(pieces[start:end]))
        if end >= len(pieces):
            break
        # Step back so that roughly `overlap` tokens are shared with the next chunk.
        back, carried = end, 0
        while back - 1 > start and carried + sizes[back - 1] <= overlap:
            back -= 1
            carried += sizes[back]
        start = back
    return chunks


def chunk_document(shop_id: str, document: Document, max_tokens: int = 500,
                   overlap: int = 80) -> list[tuple[Section, str, list[Chunk]]]:
    """Return (section, section_id, children) for every section of a document."""
    result = []
    for section in document.sections:
        sid = section_id(shop_id, document.slug, section)
        texts = split_section(section, max_tokens, overlap)
        children = [Chunk(id=f"{sid}.{index}", section_id=sid, position=index, content=text,
                          token_count=count_tokens(text)) for index, text in enumerate(texts)]
        result.append((section, sid, children))
    return result
