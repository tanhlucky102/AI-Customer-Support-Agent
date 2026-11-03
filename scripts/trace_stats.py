"""Latency and token statistics of real chat turns, from the `traces` table:

    python scripts/trace_stats.py [--shop demo_shop] [--since 2026-10-29]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import connect  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shop")
    parser.add_argument("--since", help="ISO date")
    args = parser.parse_args()
    with connect() as db:
        row = db.execute("""
            SELECT count(*) AS turns,
                   avg((mode = 'fallback')::int) AS fallback_share,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_first_ms) AS first_event_p50,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_first_ms) AS first_event_p95,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_first_token_ms) AS first_token_p50,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_first_token_ms) AS first_token_p95,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_total_ms) AS total_p50,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_total_ms) AS total_p95,
                   avg(llm_calls) AS llm_calls, avg(input_tokens) AS input_tokens,
                   avg(output_tokens) AS output_tokens,
                   sum(cached_tokens)::float / nullif(sum(input_tokens), 0) AS cached_share
            FROM traces
            WHERE (error IS NULL OR error LIKE 'fallback:%%') AND (%s::text IS NULL OR shop_id = %s)
              AND (%s::date IS NULL OR created_at >= %s::date)""",
                         (args.shop, args.shop, args.since, args.since)).fetchone()
    for key, value in row.items():
        print(f"{key:>16}: {value if value is None or isinstance(value, int) else round(float(value), 3)}")


if __name__ == "__main__":
    main()
