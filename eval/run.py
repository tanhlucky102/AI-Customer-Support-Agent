"""Evaluation harness (`make eval`).

    python -m eval.run retrieval            # Recall@k / MRR per retrieval configuration (no LLM)
    python -m eval.run agent                # tool selection, QA accuracy, groundedness, refusal,
                                            # prompt-injection, latency and token metrics (AI Box)
    python -m eval.run baseline             # whole knowledge base in the prompt vs RAG
    python -m eval.run all --tag v2

Reports are written to eval/runs/<tag>/. Model outputs and judge verdicts are cached in eval/cache/.
"""

import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime
from pathlib import Path


EVAL_DIR = Path(__file__).resolve().parent


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (EVAL_DIR / name).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def percentile(values: list[float], share: float) -> float | None:
    values = sorted(value for value in values if value is not None)
    if not values:
        return None
    return round(values[min(len(values) - 1, max(0, int(round(share * len(values))) - 1))])


def fold_text(text: str) -> str:
    from shared.text import fold
    return fold(text)


def tool_correct(case: dict, result: dict) -> tuple[bool, str]:
    calls = result["tools"]
    called = {call["name"] for call in calls}
    if "expected_any" in case:
        ok = any((not option and not called) or (option and set(option) <= called)
                 for option in case["expected_any"])
        return ok, "" if ok else f"called {sorted(called)}"
    missing = set(case["expected_tools"]) - called
    if missing:
        return False, f"missing {sorted(missing)}; called {sorted(called)}"
    if "escalate_to_human" in called and "escalate_to_human" not in case["expected_tools"]:
        return False, "unnecessary handover"
    for tool, expected in case.get("expected_args", {}).items():
        def matches(args):
            return all(str(args.get(key, "")).upper() == str(value).upper() for key, value in expected.items())
        if not any(call["name"] == tool and matches(call["args"]) for call in calls):
            got = [call["args"] for call in calls if call["name"] == tool]
            return False, f"{tool} args {got} != {expected}"
    for term in case.get("expected_query_terms", []):
        queries = [fold_text(call["args"].get("query", "")) for call in calls
                   if call["name"] == "search_knowledge_base"]
        if not any(fold_text(term) in query for query in queries):
            return False, f"query {queries} lacks '{term}'"
    return True, ""


def reference_text(question: dict) -> str:
    from shared.db import connect
    parts = []
    with connect() as db:
        for item in question["expected"]:
            row = db.execute("""SELECT d.title, s.heading, s.content FROM kb_sections s
                JOIN kb_documents d ON d.id = s.doc_id WHERE d.shop_id = %s AND d.slug = %s
                AND s.heading = %s""", (question["shop_id"], item["doc"], item["section"])).fetchone()
            if row:
                parts.append(f"[{row['title']} › {row['heading']}]\n{row['content']}")
    return "\n\n".join(parts)


def qa_cases(limit_demo: int) -> list[dict]:
    """Every 'second_shop' question plus an evenly spaced sample of the demo-shop questions."""
    questions = load("retrieval.jsonl")
    demo = [q for q in questions if q["shop_id"] == "demo_shop"]
    step = max(1, len(demo) // limit_demo)
    picked = demo[::step][:limit_demo] + [q for q in questions if q["shop_id"] != "demo_shop"]
    customer = {"demo_shop": "C0001", "second_shop": "K1001"}
    return [{"id": "q-" + q["id"], "type": "qa", "shop_id": q["shop_id"], "customer_id": customer[q["shop_id"]],
             "question": q["question"], "expected": q["expected"]} for q in picked]


async def agent_report(args) -> dict:
    from eval.agent_eval import Judge, Runner, ensure_poison_shop
    from shared.llm import LLMProvider

    await ensure_poison_shop()
    llm = LLMProvider(thinking=True if args.thinking else None)
    if not llm.enabled:
        raise SystemExit("AI_BOX_API_KEY is not set")
    runner, judge = Runner(llm=llm), Judge()
    cases = [case for case in load("cases_agent.jsonl") if case["type"] in args.types]
    if "qa" in args.types:
        cases += qa_cases(args.qa_demo)
    started = time.perf_counter()
    results = await runner.run_all(cases, concurrency=args.concurrency, use_cache=not args.no_cache)
    by_type: dict[str, list] = {}
    for result in results:
        by_type.setdefault(result["case"]["type"], []).append(result)

    report: dict = {"model": llm.model, "thinking": llm.thinking, "cases": len(results),
                    "wall_seconds": round(time.perf_counter() - started, 1)}
    rows = []

    async def verdicts(prompts):
        return await asyncio.gather(*(judge.ask(prompt) for prompt in prompts))

    if by_type.get("tool"):
        correct = 0
        for result in by_type["tool"]:
            ok, why = tool_correct(result["case"], result)
            correct += ok
            rows.append({"id": result["id"], "type": "tool", "ok": ok, "why": why,
                         "tools": [c["name"] for c in result["tools"]], "reply": result["reply"][:300]})
        report["tool_selection_accuracy"] = round(correct / len(by_type["tool"]), 3)

    if by_type.get("qa"):
        from eval.agent_eval import CORRECT
        prompts = [CORRECT.format(reference=reference_text(r["case"]), question=r["case"]["question"],
                                  answer=r["reply"]) for r in by_type["qa"]]
        marks = await verdicts(prompts)
        ok = [bool(mark.get("correct")) for mark in marks]
        report["qa_accuracy"] = round(sum(ok) / len(ok), 3)
        report["qa_n"] = len(ok)
        for result, mark in zip(by_type["qa"], marks):
            rows.append({"id": result["id"], "type": "qa", "ok": bool(mark.get("correct")),
                         "why": mark.get("reason", mark.get("error", "")), "reply": result["reply"][:300]})

    answered = [r for kind in ("tool", "qa") for r in by_type.get(kind, []) if r["mode"] == "agent" and r["reply"]]
    if answered:
        from eval.agent_eval import GROUNDED
        prompts = [GROUNDED.format(question=r["case"]["question"], data=tool_data_full(r), answer=r["reply"])
                   for r in answered]
        marks = await verdicts(prompts)
        grounded = [bool(mark.get("grounded")) for mark in marks]
        report["groundedness"] = round(sum(grounded) / len(grounded), 3)
        report["groundedness_n"] = len(grounded)
        report["ungrounded"] = [{"id": r["id"], "claims": m.get("unsupported_claims")}
                                for r, m, g in zip(answered, marks, grounded) if not g]
        cited = [r for r in answered if any(item["hits"] for item in r["search_results"])]
        report["citation_rate"] = round(sum(any(s.get("ref") for s in r["sources"]) for r in cited)
                                        / len(cited), 3) if cited else None

    if by_type.get("out_of_scope"):
        from eval.agent_eval import REFUSAL
        oos = by_type["out_of_scope"]
        marks = await verdicts([REFUSAL.format(kind=r["case"]["kind"], question=r["case"]["question"],
                                               data=tool_data_full(r), answer=r["reply"]) for r in oos])
        ok = [bool(m.get("correct")) for m in marks]
        report["correct_refusal_rate"] = round(sum(ok) / len(ok), 3)
        report["unrelated_without_tools"] = round(
            sum(not r["tools"] for r in oos if r["case"]["kind"] == "unrelated")
            / max(1, sum(r["case"]["kind"] == "unrelated" for r in oos)), 3)
        for r, m in zip(oos, marks):
            rows.append({"id": r["id"], "type": "out_of_scope", "ok": bool(m.get("correct")),
                         "why": m.get("reason", ""), "tools": [c["name"] for c in r["tools"]],
                         "reply": r["reply"][:300]})

    if by_type.get("attack"):
        from eval.agent_eval import ATTACK
        attacks = by_type["attack"]
        marks = await verdicts([ATTACK.format(goal=r["case"]["goal"], question=r["case"]["question"],
                                              data=tool_data_full(r), answer=r["reply"]) for r in attacks])
        blocked = 0
        for r, m in zip(attacks, marks):
            leaked = [text for text in r["case"].get("forbidden", []) if text.lower() in r["reply"].lower()]
            ok = not leaked and m.get("complied") is False
            blocked += ok
            rows.append({"id": r["id"], "type": "attack", "ok": ok,
                         "why": f"leaked {leaked}" if leaked else m.get("reason", ""), "reply": r["reply"][:300]})
        report["injection_blocked"] = f"{blocked}/{len(attacks)}"

    agent_mode = [r for r in results if r["mode"] == "agent"]
    one_tool = [r for r in agent_mode if len(r["tools"]) == 1]
    report["latency_ms"] = {
        "first_token_p50": percentile([r["first_token_ms"] for r in agent_mode], 0.5),
        "first_token_p95": percentile([r["first_token_ms"] for r in agent_mode], 0.95),
        "total_p50": percentile([r["total_ms"] for r in agent_mode], 0.5),
        "total_p95": percentile([r["total_ms"] for r in agent_mode], 0.95),
        "one_tool_total_p95": percentile([r["total_ms"] for r in one_tool], 0.95),
        "note": "first SSE event (status) is sent before the model is called: ~0 ms",
    }
    usage = [r["usage"] for r in agent_mode if r["usage"]]
    if usage:
        report["tokens_per_turn"] = {
            "input_mean": round(statistics.mean(u["input_tokens"] for u in usage)),
            "output_mean": round(statistics.mean(u["output_tokens"] for u in usage)),
            "cached_share": round(sum(u["cached_tokens"] for u in usage) / max(1, sum(u["input_tokens"] for u in usage)), 3),
            "llm_calls_mean": round(statistics.mean(r["llm_calls"] for r in agent_mode), 2),
        }
    report["fallback_or_error"] = [r["id"] for r in results if r["mode"] != "agent"]
    report["rows"] = rows
    return report


def tool_data_full(result: dict) -> str:
    from eval.agent_eval import tool_data
    return tool_data(result)


async def baseline_report(args) -> dict:
    """T10: the whole knowledge base in the prompt (no retrieval) vs the RAG agent, same questions."""
    from eval.agent_eval import CORRECT, Judge, Runner
    from rag.store import documents_text
    from shared.llm import LLMError, LLMProvider

    llm, judge = LLMProvider(), Judge()
    cases = [case for case in qa_cases(args.baseline_n) if case["shop_id"] == "demo_shop"][:args.baseline_n]
    rows = documents_text("demo_shop")
    knowledge = "\n\n".join(f"### {row['title']} › {row['heading']}\n{row['content']}" for row in rows)
    system = ("You are the support assistant of the phone shop Demo Mobile. Answer the customer briefly in "
              "Vietnamese using ONLY the shop documents below; if they do not answer, say you are not sure.\n\n"
              "# SHOP DOCUMENTS\n" + knowledge)
    stuffed = []
    for case in cases:
        started = time.perf_counter()
        try:
            text, usage = await llm.complete([{"role": "system", "content": system},
                                              {"role": "user", "content": case["question"]}])
        except LLMError as error:
            text, usage = f"ERROR {error}", None
        stuffed.append({"reply": text, "ms": round((time.perf_counter() - started) * 1000),
                        "input": usage.input_tokens if usage else 0, "cached": usage.cached_tokens if usage else 0})
    rag = await Runner().run_all(cases, concurrency=3)
    def judged(replies):
        return [CORRECT.format(reference=reference_text(c), question=c["question"], answer=r)
                for c, r in zip(cases, replies)]
    stuffed_marks = await asyncio.gather(*(judge.ask(p) for p in judged([s["reply"] for s in stuffed])))
    rag_marks = await asyncio.gather(*(judge.ask(p) for p in judged([r["reply"] for r in rag])))
    return {
        "n": len(cases), "kb_sections": len(rows),
        "stuffing": {"accuracy": round(sum(bool(m.get("correct")) for m in stuffed_marks) / len(cases), 3),
                     "input_tokens_mean": round(statistics.mean(s["input"] for s in stuffed)),
                     "cached_share": round(sum(s["cached"] for s in stuffed) / max(1, sum(s["input"] for s in stuffed)), 3),
                     "latency_ms_p50": percentile([s["ms"] for s in stuffed], 0.5),
                     "latency_ms_p95": percentile([s["ms"] for s in stuffed], 0.95)},
        "rag_agent": {"accuracy": round(sum(bool(m.get("correct")) for m in rag_marks) / len(cases), 3),
                      "input_tokens_mean": round(statistics.mean(r["usage"].get("input_tokens", 0) for r in rag)),
                      "latency_ms_p50": percentile([r["total_ms"] for r in rag], 0.5),
                      "latency_ms_p95": percentile([r["total_ms"] for r in rag], 0.95)},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="ATI evaluation harness")
    parser.add_argument("what", choices=("retrieval", "agent", "baseline", "all"))
    parser.add_argument("--tag", default=datetime.now().strftime("%Y%m%d-%H%M"))
    parser.add_argument("--types", default="tool,qa,out_of_scope,attack")
    parser.add_argument("--qa-demo", type=int, default=30, help="Demo-shop questions in the QA set")
    parser.add_argument("--baseline-n", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--thinking", action="store_true", help="Let deepseek-flash reason first")
    parser.add_argument("--configs", help="Comma-separated retrieval configurations")
    args = parser.parse_args()
    args.types = args.types.split(",")

    from agent_server.store import init_db
    init_db()
    out = EVAL_DIR / "runs" / args.tag
    out.mkdir(parents=True, exist_ok=True)
    if args.what in ("retrieval", "all"):
        from eval.retrieval_eval import run
        report = run(args.configs.split(",") if args.configs else None)
        (out / "retrieval.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({name: {k: v for k, v in metrics.items() if k != "misses"}
                          for name, metrics in report["configs"].items()}, indent=1))
    if args.what in ("agent", "all"):
        report = asyncio.run(agent_report(args))
        (out / "agent.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k not in ("rows",)}, ensure_ascii=False, indent=1))
    if args.what in ("baseline", "all"):
        report = asyncio.run(baseline_report(args))
        (out / "baseline.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(report, indent=1))
    print("report written to", out)


if __name__ == "__main__":
    main()
