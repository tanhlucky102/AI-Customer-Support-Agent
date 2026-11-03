"""Retrieval metrics on eval/retrieval.jsonl (no LLM calls): Recall@1/5, MRR@10 per configuration.

A question is a hit at rank r if the r-th returned section is one of its expected sections
(document slug + '## ' heading). The τ analysis compares the best reranker score of answerable
questions with that of unanswerable shop questions (out_of_scope/unknown cases).
"""

import json
import math
import time
from pathlib import Path

from rag.search import SearchConfig, search
from shared.db import connect

EVAL_DIR = Path(__file__).resolve().parent

CONFIGS = {
    "vector": SearchConfig(vector=True, keyword=None, rerank=False),
    "bm25": SearchConfig(vector=False, keyword="bm25", rerank=False),
    "tsrank": SearchConfig(vector=False, keyword="tsrank", rerank=False),
    "hybrid": SearchConfig(vector=True, keyword="bm25", rerank=False),
    "hybrid_tsrank": SearchConfig(vector=True, keyword="tsrank", rerank=False),
    "hybrid_rerank": SearchConfig(vector=True, keyword="bm25", rerank=True),
    # The earlier setting: twice the reranker's work (it dominates search time).
    "hybrid_rerank_top20": SearchConfig(vector=True, keyword="bm25", rerank=True, rerank_top=20),
}


def nearest_rank(sorted_values: list[float], share: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, max(0, math.ceil(share * len(sorted_values)) - 1))]


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def contextual_share() -> float:
    with connect() as db:
        row = db.execute("SELECT avg((context_prefix <> '')::int)::float AS share FROM kb_chunks "
                         "JOIN kb_documents d ON d.id = kb_chunks.doc_id WHERE d.origin = 'file'").fetchone()
    return round(row["share"] or 0.0, 3)


def evaluate(config: SearchConfig, questions: list[dict]) -> dict:
    hits1 = hits5 = gated5 = 0
    reciprocal, latencies, misses = 0.0, [], []
    tau = config.tau if config.rerank else config.tau_no_rerank
    for question in questions:
        expected = {(item["doc"], item["section"]) for item in question["expected"]}
        started = time.perf_counter()
        results = search(question["question"], question["shop_id"], k=10, config=config,
                         apply_threshold=False)
        latencies.append((time.perf_counter() - started) * 1000)
        ranks = [rank for rank, item in enumerate(results, start=1)
                 if (item["doc"], item["section"]) in expected]
        first = ranks[0] if ranks else None
        hits1 += first == 1
        hits5 += bool(first and first <= 5)
        # What a search with the τ gate returns for the customer's raw words (the fallback case).
        top = results[0].get("score", 0.0) if results else 0.0
        confidence = top if config.rerank else max((item.get("similarity", 0.0) for item in results), default=0.0)
        gated5 += bool(first and first <= 5 and confidence >= tau)
        reciprocal += 1 / first if first else 0.0
        if not first or first > 5:
            misses.append({"id": question["id"], "question": question["question"],
                           "expected": sorted(f"{d}#{s}" for d, s in expected),
                           "got": [f"{item['doc']}#{item['section']}" for item in results[:3]]})
    n = len(questions)
    latencies.sort()
    return {"n": n, "recall@1": round(hits1 / n, 3), "recall@5": round(hits5 / n, 3),
            "recall@5_gated": round(gated5 / n, 3),
            "mrr@10": round(reciprocal / n, 3),
            "latency_ms_p50": round(nearest_rank(latencies, 0.5)),
            "latency_ms_p95": round(nearest_rank(latencies, 0.95)),
            "misses": misses}


def tau_analysis(questions: list[dict], unknown: list[dict]) -> dict:
    """Best reranker score for answerable vs unanswerable questions, and the accuracy of each τ."""
    config = CONFIGS["hybrid_rerank"]
    answerable = [search(q["question"], q["shop_id"], k=1, config=config, apply_threshold=False)
                  for q in questions]
    answerable = [hits[0]["score"] if hits else 0.0 for hits in answerable]
    unanswerable = [search(q["question"], q["shop_id"], k=1, config=config, apply_threshold=False)
                    for q in unknown]
    unanswerable = [hits[0]["score"] if hits else 0.0 for hits in unanswerable]
    table = []
    for tau in (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5):
        kept = sum(score >= tau for score in answerable) / len(answerable)
        rejected = sum(score < tau for score in unanswerable) / len(unanswerable)
        table.append({"tau": tau, "answerable_kept": round(kept, 3),
                      "unanswerable_rejected": round(rejected, 3)})
    return {"answerable_scores": sorted(round(s, 3) for s in answerable),
            "unanswerable_scores": sorted(round(s, 3) for s in unanswerable), "table": table}


def run(config_names: list[str] | None = None, with_tau: bool = True) -> dict:
    questions = load(EVAL_DIR / "retrieval.jsonl")
    unknown = [case for case in load(EVAL_DIR / "cases_agent.jsonl")
               if case["type"] == "out_of_scope" and case.get("kind") == "unknown"]
    report = {"contextual_share": contextual_share(), "configs": {}}
    for name in config_names or list(CONFIGS):
        report["configs"][name] = evaluate(CONFIGS[name], questions)
    if with_tau:
        report["tau"] = tau_analysis(questions, unknown)
    return report
