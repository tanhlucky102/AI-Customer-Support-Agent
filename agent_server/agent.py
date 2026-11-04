"""Agent core (contract C5): agent.run(ctx, history, message) -> async stream of events.

Reason -> Act -> Observe: the model either answers or asks for tools; tool results go back to it,
at most MAX_ITERATIONS model calls per turn. The core knows nothing about HTTP or the widget, so the
chat endpoint and the evaluation harness call exactly the same function.

Events: {"type": "status", "message"} | {"type": "token", "text"} | {"type": "citations", "sources"}
        | {"type": "replace", "text"} (the answer so far, without text written before a tool call)
        | {"type": "done", "reply", "mode", "tools", "sources", "usage", "llm_calls",
           "first_token_ms"} | {"type": "error", "message"}
"""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator

from agent_server import fallback
from agent_server.prompts import context_message, system_prompt
from agent_server.tools import BY_NAME, TurnContext, definitions, execute, toolset_for
from shared.llm import LLMError, LLMProvider, Usage
from shared.text import looks_english

MAX_ITERATIONS = 5
# "[1]", and also "[1, 2]" or "[1-3]" when the model groups its references.
CITATION = re.compile(r"\[(\d{1,2}(?:\s*[,–-]\s*\d{1,2})*)\]")


def citation_numbers(text: str) -> set[int]:
    numbers = set()
    for group in CITATION.findall(text):
        for part in re.split(r"\s*,\s*", group):
            ends = [int(value) for value in re.split(r"\s*[–-]\s*", part)]
            if len(ends) == 2 and 0 <= ends[1] - ends[0] <= 10:
                numbers.update(range(ends[0], ends[1] + 1))
            else:
                numbers.update(ends)
    return numbers


def history_messages(history: list[dict]) -> list[dict]:
    """Earlier turns as plain text; old citation numbers are dropped so they cannot collide."""
    return [{"role": row["role"], "content": CITATION.sub("", row["content"]).strip()}
            for row in history[-8:] if row["role"] in ("user", "assistant") and row["content"]]


def cited_sources(reply: str, ctx: TurnContext) -> list[dict]:
    numbers = sorted(citation_numbers(reply) & set(ctx.sources))
    sources = [{"ref": number, **ctx.sources[number]} for number in numbers]
    return sources + ctx.links


async def run(ctx: TurnContext, history: list[dict], message: str,
              llm: LLMProvider | None = None) -> AsyncIterator[dict]:
    llm = llm or LLMProvider()
    ctx.english = looks_english(message)
    tools = toolset_for(ctx.adapter, guest=ctx.guest)
    tool_definitions = definitions(tools)
    # The date changes every minute, so it comes after the history: the system prompt, tools and
    # earlier turns stay a stable prefix that the gateway can cache.
    messages = [{"role": "system", "content": system_prompt(ctx.shop, tools, guest=ctx.guest)},
                *history_messages(history),
                {"role": "system", "content": context_message(message=message)},
                {"role": "user", "content": message}]
    usage, calls, reply = Usage(), [], ""
    model = llm.model
    seen: dict[tuple[str, str], tuple[dict, object]] = {}  # (tool, arguments) already run this turn
    first_token_ms, started = None, time.perf_counter()
    yield {"type": "status", "message": "Đang xử lý câu hỏi..."}

    if not llm.enabled:
        async for event in _fallback(ctx, message, "no_llm", usage, calls):
            yield event
        return
    try:
        for iteration in range(MAX_ITERATIONS):
            last = iteration == MAX_ITERATIONS - 1
            result, kept = None, reply
            async for event in llm.stream(messages, tool_definitions,
                                          tool_choice="none" if last else "auto"):
                if event["type"] == "token":
                    if first_token_ms is None:
                        first_token_ms = round((time.perf_counter() - started) * 1000)
                    reply += event["text"]
                    yield event
                else:
                    result = event["result"]
            usage.add(result.usage)
            model = result.model or model
            if not result.tool_calls or last:
                break
            if result.content:  # A preamble ("I'll check…") despite the rule: take it back.
                reply = kept
                first_token_ms = first_token_ms if kept else None
                yield {"type": "replace", "text": reply}
            messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": call["id"], "type": "function",
                 "function": {"name": call["name"], "arguments": valid_json(call["arguments"])}}
                for call in result.tool_calls]})
            final_text = None
            for call in result.tool_calls:
                tool = BY_NAME.get(call["name"])
                if tool in tools:
                    yield {"type": "status", "message": tool.status}

            async def run_call(call: dict):
                key = (call["name"], valid_json(call["arguments"]))
                if key not in seen:  # The same lookup twice in one turn is answered from the first.
                    seen[key] = await execute(ctx, tools, call["name"], call["arguments"])
                return seen[key]

            # Calls requested together (an order and a policy) run concurrently.
            results = await asyncio.gather(*(run_call(call) for call in result.tool_calls))
            for call, (args, outcome) in zip(result.tool_calls, results):
                # The output is for evaluation; the chat endpoint keeps only name/args/ok in traces.
                calls.append({"name": call["name"], "args": args, "ok": outcome.ok,
                              "output": outcome.content})
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(outcome.content, ensure_ascii=False, default=str)})
                final_text = final_text or outcome.final_text
            if final_text:  # Escalation ends the turn with a fixed, accurate message.
                reply += final_text
                yield {"type": "token", "text": final_text}
                break
    except LLMError as error:
        if not reply:
            async for event in _fallback(ctx, message, str(error), usage, calls):
                yield event
            return
        yield {"type": "error", "message": "Chatbot bị gián đoạn. Vui lòng thử lại.",
               "detail": str(error)}
        return
    if not reply.strip():
        reply = no_answer_text(ctx)
        yield {"type": "token", "text": reply}
    sources = cited_sources(reply, ctx)
    if sources:
        yield {"type": "citations", "sources": sources}
    yield {"type": "done", "reply": reply, "mode": "agent", "tools": calls, "sources": sources,
           "usage": usage.__dict__, "llm_calls": usage.calls, "first_token_ms": first_token_ms,
           "model": model}


def no_answer_text(ctx: TurnContext) -> str:
    """When the model produced nothing: a handover offer, except for guests, who have none."""
    if ctx.guest:
        return ("Sorry, I could not answer this. Please contact the shop through the details on its website."
                if ctx.english else "Xin lỗi, mình chưa trả lời được câu này. Bạn vui lòng liên hệ shop "
                "qua thông tin trên website nhé.")
    return ("Sorry, I could not answer this. Would you like me to pass it to our support staff?"
            if ctx.english else "Xin lỗi, mình chưa trả lời được câu này. Bạn muốn mình chuyển cho "
            "nhân viên hỗ trợ không?")


def valid_json(arguments: str) -> str:
    """Echo the model's arguments back only if they parse; strict gateways reject broken JSON."""
    try:
        json.loads(arguments or "{}")
        return arguments or "{}"
    except ValueError:
        return "{}"


async def _fallback(ctx: TurnContext, message: str, reason: str, usage: Usage | None = None,
                    calls: list | None = None) -> AsyncIterator[dict]:
    status, reply, sources = await fallback.answer(ctx, message)
    yield {"type": "status", "message": status}
    for start in range(0, len(reply), 40):
        yield {"type": "token", "text": reply[start:start + 40]}
    if sources:
        yield {"type": "citations", "sources": sources}
    usage = usage or Usage()  # Calls made before the model failed still count in the trace.
    yield {"type": "done", "reply": reply, "mode": "fallback", "tools": calls or [], "sources": sources,
           "usage": usage.__dict__, "llm_calls": usage.calls, "first_token_ms": None, "model": None,
           "fallback_reason": reason}
