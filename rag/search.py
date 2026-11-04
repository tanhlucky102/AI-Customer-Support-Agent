"""Hybrid retrieval (contract C1).

query -> (a) vector search, BGE-M3 cosine on HNSW, top 20 chunks
      -> (b) keyword search, BM25 (or Postgres ts_rank_cd), top 20 chunks
      -> Reciprocal Rank Fusion, k = 60
      -> parent sections of the fused chunks -> cross-encoder rerank (bge-reranker-v2-m3)
      -> top k sections; [] ("no_results") when the best score is below τ
"""

from dataclasses import dataclass

from rag import bm25, store
from rag.models import embedder, reranker
from shared.settings import env, env_flag, env_float, env_int
from shared.text import query_terms


@dataclass(frozen=True)
class SearchConfig:
    vector: bool = True
    keyword: str | None = "bm25"  # "bm25", "tsrank" or None
    rerank: bool = True
    candidates: int = 20
    rrf_k: int = 60
    # τ: minimum reranker probability, or minimum cosine similarity when reranking is off.
    tau: float = 0.1
    tau_no_rerank: float = 0.45
    # Sections passed to the cross-encoder. 10 instead of 20 cut search time by ~40% and did not lower
    # Recall@5 (eval/results/retrieval_v10.json); the reranker dominates search time.
    rerank_top: int = 10

    @classmethod
    def from_env(cls) -> "SearchConfig":
        keyword = env("RAG_KEYWORD", "bm25")
        return cls(vector=env_flag("RAG_VECTOR", True), keyword=None if keyword == "none" else keyword,
                   rerank=env_flag("RAG_RERANK", True), candidates=env_int("RAG_CANDIDATES", 20),
                   tau=env_float("RAG_TAU", 0.1), tau_no_rerank=env_float("RAG_TAU_NO_RERANK", 0.45),
                   rerank_top=env_int("RAG_RERANK_TOP", 10))


def rrf(rankings: list[list[str]], k: int) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def passage(section: dict) -> str:
    return f"{section['title']} › {section['heading']}\n{section['content']}"


def search(query: str, shop_id: str, category: str | None = None, k: int = 5,
           config: SearchConfig | None = None, apply_threshold: bool = True,
           relaxed: bool = False) -> list[dict]:
    """Return up to k passages: {chunk_id, section_id, content, doc_title, section, url, category,
    score}. An empty list means nothing relevant enough was found.

    `category` is part of contract C1 and used by the retrieval evaluation; the agent's tool passes
    None since v3, because a guessed category excluded the right document.

    τ was calibrated on the agent's rewritten queries. A customer's raw words ("don 450k co mat phi
    ship ko") score far lower against the same right section, so `relaxed` (the fallback, which has
    no model to rewrite the question) also accepts the top section when the vector search and the
    keyword search both ranked it first: two independent retrievers agreeing."""
    config = config or SearchConfig.from_env()
    tau = config.tau if config.rerank else config.tau_no_rerank
    results, confidence, agreed = _search(query, shop_id, category, k, config)
    if category and confidence < tau:
        # The model may have guessed the category; search the whole knowledge base instead.
        results, confidence, agreed = _search(query, shop_id, None, k, config)
    if apply_threshold and confidence < tau:
        return results[:1] if relaxed and agreed else []
    return results


def _search(query: str, shop_id: str, category: str | None, k: int,
            config: SearchConfig) -> tuple[list[dict], float, bool]:
    """(results, confidence, agreed): agreed = vector and keyword search both rank the top result first."""
    rankings, similarity, section_of = [], {}, {}
    if config.vector:
        vector = embedder().encode([query])[0]
        hits = store.vector_candidates(shop_id, vector, config.candidates, category)
        rankings.append([hit["id"] for hit in hits])
        similarity = {hit["id"]: float(hit["similarity"]) for hit in hits}
        section_of.update({hit["id"]: hit["section_id"] for hit in hits})
    if config.keyword:
        terms = query_terms(query)
        if config.keyword == "tsrank":
            hits = store.tsrank_candidates(shop_id, terms, config.candidates, category)
        else:
            hits = bm25.index_for(shop_id).search(terms, config.candidates, category)
        rankings.append([hit["id"] for hit in hits])
        section_of.update({hit["id"]: hit["section_id"] for hit in hits})
    fused = rrf(rankings, config.rrf_k) if len(rankings) > 1 else {
        item: 1.0 / (config.rrf_k + rank) for rank, item in enumerate(rankings[0] if rankings else [], 1)}

    # Parent-document retrieval: a section is as good as its best child chunk.
    by_section: dict[str, tuple[float, str]] = {}
    for chunk_id, score in sorted(fused.items(), key=lambda item: item[1], reverse=True):
        section_id = section_of[chunk_id]
        if section_id not in by_section:
            by_section[section_id] = (score, chunk_id)
    ordered = list(by_section.items())[:min(config.candidates, config.rerank_top if config.rerank else
                                            config.candidates)]
    firsts = {section_of[ranking[0]] for ranking in rankings if ranking}
    sections = store.sections([section_id for section_id, _ in ordered])
    results = []
    for section_id, (fused_score, chunk_id) in ordered:
        section = sections.get(section_id)
        if not section:
            continue
        url = section["source_url"]
        if url and url.endswith(f"/help/{section['slug']}"):
            url += f"#muc-{section_id.rsplit('#', 1)[1]}"  # Anchor of the section on the help page.
        results.append({
            "chunk_id": chunk_id, "section_id": section_id, "content": passage(section),
            "doc_title": section["title"], "doc": section["slug"], "section": section["heading"],
            "url": url, "category": section["category"],
            "rrf": fused_score,
            "similarity": max((value for key, value in similarity.items()
                               if section_of.get(key) == section_id), default=0.0),
        })
    if not results:
        return [], 0.0, False
    if config.rerank:
        scores = reranker().score(query, [item["content"] for item in results])
        for item, score in zip(results, scores):
            item["score"] = score
        results.sort(key=lambda item: item["score"], reverse=True)
        agreed = len(rankings) > 1 and firsts == {results[0]["section_id"]}
        return results[:k], results[0]["score"], agreed
    # Without a reranker the order is the fused order and the gate uses cosine similarity.
    for item in results:
        item["score"] = item["similarity"] if config.vector else item["rrf"]
    confidence = max(item["similarity"] for item in results) if config.vector else 1.0
    agreed = len(rankings) > 1 and firsts == {results[0]["section_id"]}
    return results[:k], confidence, agreed

