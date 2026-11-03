"""LLMProvider: every model call goes through LiteLLM, so changing provider is configuration only.

LLM_MODEL uses LiteLLM's naming; the default reaches AI Box's OpenAI-compatible endpoint:
    LLM_MODEL=openai/ds/deepseek-flash   AI_BOX_BASE_URL=https://api.ai-box.vn/v1   AI_BOX_API_KEY=sk-...
"""

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
                 thinking: bool | None = None):
        self.model = model or env("LLM_MODEL", DEFAULT_MODEL)
        if self.model.startswith("gemini/"):  # The old Week-1 setting; AI Box is the team's choice.
            self.model = DEFAULT_MODEL
        self.api_base = api_base or env("AI_BOX_BASE_URL", "https://api.ai-box.vn/v1")
        self.api_key = api_key if api_key is not None else env("AI_BOX_API_KEY")
        self.timeout = timeout or env_float("LLM_TIMEOUT_SECONDS", 15.0)
        self.temperature = temperature
        # deepseek-flash reasons before answering unless told not to; off keeps latency low.
        self.thinking = env_flag("LLM_THINKING", False) if thinking is None else thinking

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _kwargs(self, messages, tools, tool_choice) -> dict:
        kwargs = {"model": self.model, "messages": messages, "api_base": self.api_base,
                  "api_key": self.api_key, "timeout": self.timeout, "temperature": self.temperature,
                  "num_retries": 0}
        if not self.thinking:
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
        started = time.perf_counter()
        for attempt in range(2):
            result = LLMResult()
            calls: dict[int, dict] = {}
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
                        # Chunks of one call share its index; without an index a new id starts a call.
                        key = call.index if call.index is not None else (
                            len(calls) if call.id and all(c["id"] != call.id for c in calls.values())
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
                if result.content or attempt == 1 or not transient(error):
                    raise LLMError(f"{type(error).__name__}: {str(error)[:200]}") from None
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
        kwargs = self._kwargs(messages, None, None)
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as error:
            raise LLMError(f"{type(error).__name__}: {str(error)[:200]}") from None
        return (response.choices[0].message.content or "").strip(), _usage(response.usage)


def transient(error: Exception) -> bool:
    """Worth one retry: stalls, dropped connections, overload; not bad requests or bad keys."""
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    return isinstance(error, (TimeoutError, ConnectionError)) or any(
        word in type(error).__name__ for word in ("Timeout", "Connection", "Unavailable", "RateLimit"))


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
