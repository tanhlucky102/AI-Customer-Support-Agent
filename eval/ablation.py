"""Retrieval ablation: index variants x search configurations (no LLM calls except cached context).

    python -m eval.ablation

Each index variant re-indexes both shops, then every search configuration of
eval.retrieval_eval is measured. The production variant (no context sentences, 500-token chunks:
the best measured) runs last, so the database is left in its normal state.
"""

import asyncio
import json
from pathlib import Path

from agent_server.store import init_db, shop_by_id
from eval import retrieval_eval
from rag.ingest import ingest_dir
from shared.settings import ROOT

# The last variant is the production setting, so the database ends in its normal state.
VARIANTS = [
    ("contextual_T500", {"contextual": True, "max_tokens": 500, "overlap": 80}),
    ("contextual_T250", {"contextual": True, "max_tokens": 250, "overlap": 50}),
    ("plain_T250", {"contextual": False, "max_tokens": 250, "overlap": 50}),
    ("plain_T500", {"contextual": False, "max_tokens": 500, "overlap": 80}),
]
OUT = Path(__file__).resolve().parent / "runs" / "ablation"


def index(options: dict) -> dict:
    stats = {}
    for shop_id in ("demo_shop", "second_shop"):
        shop = shop_by_id(shop_id)
        stats[shop_id] = asyncio.run(ingest_dir(shop_id, shop["shop_url"], ROOT / "data" / "kb" / shop_id,
                                                force=True, **options))
    return stats


def main() -> None:
    init_db()
    OUT.mkdir(parents=True, exist_ok=True)
    table = {}
    for name, options in VARIANTS:
        stats = index(options)
        report = retrieval_eval.run(with_tau=name == VARIANTS[-1][0])
        report["index"] = {"options": options, "stats": stats}
        (OUT / f"{name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        table[name] = {config: {key: metrics[key] for key in ("recall@1", "recall@5", "mrr@10",
                                                              "latency_ms_p50")}
                       for config, metrics in report["configs"].items()}
        print(name, json.dumps(table[name], indent=1), flush=True)
    (OUT / "summary.json").write_text(json.dumps(table, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
