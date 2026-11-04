"""LLMProvider: every model call goes through LiteLLM, so changing provider is configuration only.

LLM_MODEL uses LiteLLM's naming; the default reaches AI Box's OpenAI-compatible endpoint:
    LLM_MODEL=openai/ds/deepseek-flash   AI_BOX_BASE_URL=https://api.ai-box.vn/v1   AI_BOX_API_KEY=sk-...

Failover: when the primary model fails before any text was streamed (after its own retry), the turn
goes to a second model, if one is configured (any LiteLLM model, e.g. a local one through Ollama):
    LLM_FALLBACK_MODEL=ollama_chat/qwen2.5:7b   LLM_FALLBACK_BASE_URL=http://localhost:11434
    LLM_FALLBACK_MODEL=openai/gpt-4o-mini       LLM_FALLBACK_API_KEY=sk-...
Only when both fail does the agent use its rule-based answers.
"""

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from shared.settings import env, env_flag, env_float

# Use LiteLLM's bundled model table instead of downloading it on every start.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

DEFAULT_MODEL = "openai/" + env("AI_BOX_MODEL", "ds/deepseek-flash")


class LLMError(Exception):
    """The provider failed or timed out; callers switch to the rule-based fallback."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    calls: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_tokens += other.cached_tokens
        self.calls += other.calls


@dataclass
class LLMResult:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage = field(default_factory=Usage)
    first_token_ms: int | None = None
    model: str | None = None  # the model that answered (the fallback's, after a failover)


def _usage(raw) -> Usage:
    if raw is None:
        return Usage(calls=1)
    get = raw.get if isinstance(raw, dict) else lambda key, default=None: getattr(raw, key, default)
    details = get("prompt_tokens_details")
    cached = 0
    if details is not None:
        cached = (details.get("cached_tokens") if isinstance(details, dict)
                  else getattr(details, "cached_tokens", 0)) or 0
    # DeepSeek-style gateways report the cache hit separately.
    cached = cached or get("prompt_cache_hit_tokens", 0) or 0
    return Usage(input_tokens=get("prompt_tokens", 0) or 0,
                 output_tokens=get("completion_tokens", 0) or 0, cached_tokens=cached, calls=1)


class LLMProvider:
    def __init__(self, model: str | None = None, api_base: str | None = None,
                 api_key: str | None = None, timeout: float | None = None, temperature: float = 0.2,
                 thinking: bool | None = None, fallback: "LLMProvider | None | bool" = True):
        self.model = model or env("LLM_MODEL", DEFAULT_MODEL)
        self.api_base = api_base or env("AI_BOX_BASE_URL", "https://api.ai-box.vn/v1")
        self.api_key = api_key if api_key is not None else env("AI_BOX_API_KEY")
        self.timeout = timeout or env_float("LLM_TIMEOUT_SECONDS", 30.0)
        self.temperature = temperature
        # deepseek-flash reasons before answering unless told not to; off keeps latency low.
        self.thinking = env_flag("LLM_THINKING", False) if thinking is None else thinking
        # fallback=True reads LLM_FALLBACK_* from the environment; False/None means no failover.
        self.fallback = self.from_fallback_env() if fallback is True else (fallback or None)

    @classmethod
    def from_fallback_env(cls) -> "LLMProvider | None":
        model = env("LLM_FALLBACK_MODEL")
        if not model:
            return None
        return cls(model=model, api_base=env("LLM_FALLBACK_BASE_URL") or None,
                   api_key=env("LLM_FALLBACK_API_KEY", "none"),
                   timeout=env_float("LLM_FALLBACK_TIMEOUT_SECONDS", 60.0), fallback=False)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) or bool(self.fallback and self.fallback.enabled)

    def _kwargs(self, messages, tools, tool_choice) -> dict:
        kwargs = {"model": self.model, "messages": messages, "api_key": self.api_key,
                  "timeout": self.timeout, "temperature": self.temperature, "num_retries": 0}
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if not self.thinking and "deepseek" in self.model:  # other providers reject this parameter
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        return kwargs

    async def stream(self, messages: list[dict], tools: list[dict] | None = None,
                     tool_choice: str = "auto") -> AsyncIterator[dict]:
        """Yield {"type": "token", "text"} as text arrives, then {"type": "result", "result"}.

        A call that fails before any text was streamed (gateway stall, dropped connection) is
        retried once; after text has reached the customer a failure is final.
        """
        import litellm

        if not self.enabled:
            raise LLMError("no API key configured")
        if not self.api_key:  # only the fallback is configured
            async for event in self.fallback.stream(messages, tools, tool_choice):
                yield event
            return
        started = time.perf_counter()
        for attempt in range(2):
            result = LLMResult(model=self.model)
            calls: dict[int, dict] = {}
            slot_of_index: dict[int, int] = {}  # provider index -> slot; a new id gets a new slot
            try:
                response = await litellm.acompletion(**self._kwargs(messages, tools, tool_choice),
                                                     stream=True, stream_options={"include_usage": True})
                async for chunk in response:
                    if getattr(chunk, "usage", None):
                        result.usage = _usage(chunk.usage)
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    if getattr(delta, "content", None):
                        if result.first_token_ms is None:
                            result.first_token_ms = round((time.perf_counter() - started) * 1000)
                        result.content += delta.content
                        yield {"type": "token", "text": delta.content}
                    for call in getattr(delta, "tool_calls", None) or []:
                        # Chunks of one call share its index; a new id (even on a reused index, as
                        # some gateways do for parallel calls) starts a new call.
                        if call.index is not None:
                            key = slot_of_index.get(call.index)
                            if key is None or (call.id and calls[key]["id"] and calls[key]["id"] != call.id):
                                key = slot_of_index[call.index] = len(calls)
                        else:
                            key = (len(calls) if call.id and all(c["id"] != call.id for c in calls.values())
                                   else max(calls, default=0))
                        slot = calls.setdefault(key, {"id": None, "name": "", "arguments": ""})
                        if call.id:
                            slot["id"] = call.id
                        if call.function and call.function.name:
                            slot["name"] = call.function.name  # Sent once, or repeated in full.
                        if call.function and call.function.arguments:
                            slot["arguments"] += call.function.arguments
                    if choice.finish_reason:
                        result.finish_reason = choice.finish_reason
                break
            except Exception as error:  # litellm wraps timeouts, HTTP and connection errors.
                if not result.content and self.fallback and (attempt == 1 or not transient(error)):
                    # Nothing reached the customer yet: let the second model answer the whole call.
                    async for event in self.fallback.stream(messages, tools, tool_choice):
                        yield event
                    return
                if result.content or attempt == 1 or not transient(error):
                    raise LLMError(f"{type(error).__name__}: {str(error)[:200]}") from None
                if rate_limited(error):  # Retrying at once would only be refused again.
                    await asyncio.sleep(1.5)
        result.usage.calls = 1
        result.tool_calls = [
            {"id": slot["id"] or f"call_{index}", "name": slot["name"], "arguments": slot["arguments"]}
            for index, slot in sorted(calls.items()) if slot["name"]]
        yield {"type": "result", "result": result}

    async def complete(self, messages: list[dict], max_tokens: int | None = None,
                       json_mode: bool = False) -> tuple[str, Usage]:
        """One non-streaming call; used for chunk context generation and LLM-as-judge."""
        import litellm

        if not self.enabled:
            raise LLMError("no API key configured")
        if not self.api_key:
            return await self.fallback.complete(messages, max_tokens, json_mode)
        kwargs = self._kwargs(messages, None, None)
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as error:
            if self.fallback:
                return await self.fallback.complete(messages, max_tokens, json_mode)
            raise LLMError(f"{type(error).__name__}: {str(error)[:200]}") from None
        return (response.choices[0].message.content or "").strip(), _usage(response.usage)


def transient(error: Exception) -> bool:
    """Worth one retry: stalls, dropped connections, overload; not bad requests or bad keys."""
    # litellm's Timeout carries status 408, so the error type is checked before the status code.
    if isinstance(error, (TimeoutError, ConnectionError)) or any(
            word in type(error).__name__ for word in ("Timeout", "Connection", "Unavailable", "RateLimit")):
        return True
    status = getattr(error, "status_code", None)
    return isinstance(status, int) and (status in (408, 429) or status >= 500)


def rate_limited(error: Exception) -> bool:
    return "RateLimit" in type(error).__name__ or getattr(error, "status_code", None) == 429


def parse_json(text: str) -> dict:
    """Accept a JSON object even if the model wrapped it in a Markdown fence."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])
