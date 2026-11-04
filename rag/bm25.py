"""Okapi BM25 (k1 = 1.2, b = 0.75) over one shop's chunks, kept in memory.

Postgres' ts_rank_cd has no IDF, so a word like "điện thoại" that appears everywhere counts as much
as a rare one like "eSIM". A shop has at most a few thousand chunks, so exact BM25 in memory is
cheap; the index is rebuilt when the shop's documents change (rag.store.signature).
"""

import math
import threading
from collections import Counter

from rag import store
from shared.text import terms

K1, B = 1.2, 0.75


class BM25Index:
    def __init__(self, rows: list[dict]):
        self.ids = [row["id"] for row in rows]
        self.sections = {row["id"]: row["section_id"] for row in rows}
        self.categories = {row["id"]: row["category"] for row in rows}
        self.tf = [Counter(terms(row["text"])) for row in rows]
        self.lengths = [sum(counts.values()) for counts in self.tf]
        self.avgdl = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        df = Counter(term for counts in self.tf for term in counts)
        n = len(rows)
        self.idf = {term: math.log((n - freq + 0.5) / (freq + 0.5) + 1) for term, freq in df.items()}

    def search(self, query: list[str], limit: int, category: str | None = None) -> list[dict]:
        scored = []
        for index, counts in enumerate(self.tf):
            if category and self.categories[self.ids[index]] != category:
                continue
            score = 0.0
            norm = K1 * (1 - B + B * self.lengths[index] / (self.avgdl or 1))
            for term in query:
                freq = counts.get(term)
                if freq:
                    score += self.idf[term] * freq * (K1 + 1) / (freq + norm)
            if score > 0:
                scored.append((score, self.ids[index]))
        scored.sort(reverse=True)
        return [{"id": chunk_id, "section_id": self.sections[chunk_id], "score": score}
                for score, chunk_id in scored[:limit]]


_cache: dict[str, tuple[str, BM25Index]] = {}
_lock = threading.Lock()


def index_for(shop_id: str) -> BM25Index:
    current = store.signature(shop_id)
    with _lock:
        entry = _cache.get(shop_id)
        if entry and entry[0] == current:
            return entry[1]
    index = BM25Index(store.chunk_texts(shop_id))
    with _lock:
        _cache[shop_id] = (current, index)
    return index
