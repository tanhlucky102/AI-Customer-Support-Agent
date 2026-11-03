"""Behaviour when the model gateway stalls, and the rule-based fallback for any shop's order codes."""

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ["SECOND_SHOP_API_KEY"] = "test-second-key"

from tests import support  # noqa: E402,F401

import httpx  # noqa: E402

from agent_server import fallback  # noqa: E402
from agent_server.commerce import RestCommerceAdapter  # noqa: E402
from agent_server.tools import TurnContext  # noqa: E402
from examples.second_shop.app import app as second_shop  # noqa: E402
from shared.llm import LLMError, LLMProvider  # noqa: E402

SECOND_CONFIG = json.loads((support.ROOT / "examples" / "second_shop" / "commerce.json")
                           .read_text(encoding="utf-8"))


def chunk(content=None, finish=None):
    delta = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


class Stream:
    def __init__(self, parts, fail_after=None):
        self.parts, self.fail_after = parts, fail_after

    def __aiter__(self):
        return self._run()

    async def _run(self):
        for index, part in enumerate(self.parts):
            if self.fail_after is not None and index == self.fail_after:
                raise TimeoutError("stalled")
            yield part


async def collect(provider):
    return [event async for event in provider.stream([{"role": "user", "content": "hi"}])]


class LLMRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_call_that_fails_before_any_text_is_retried_once(self):
        responses = [TimeoutError("connect stalled"), Stream([chunk("Xin "), chunk("chào", "stop")])]

        async def fake(**kwargs):
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        with patch("litellm.acompletion", side_effect=fake):
            events = await collect(LLMProvider(api_key="k"))
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "token"), "Xin chào")
        self.assertEqual(responses, [])

    def test_gateway_timeouts_are_transient_but_bad_requests_are_not(self):
        import litellm

        from shared.llm import transient
        timeout = litellm.Timeout(message="stalled", model="m", llm_provider="openai")
        self.assertEqual(timeout.status_code, 408)  # A status check alone would refuse to retry it.
        self.assertTrue(transient(timeout))
        self.assertTrue(transient(SimpleNamespace(status_code=503)))
        self.assertFalse(transient(litellm.AuthenticationError("bad key", "openai", "m")))
        self.assertFalse(transient(litellm.BadRequestError("bad", "m", "openai")))

    async def test_a_failure_after_text_was_streamed_is_not_retried(self):
        calls = 0

        async def fake(**kwargs):
            nonlocal calls
            calls += 1
            return Stream([chunk("Xin "), chunk("chào")], fail_after=1)

        with patch("litellm.acompletion", side_effect=fake), self.assertRaises(LLMError):
            await collect(LLMProvider(api_key="k"))
        self.assertEqual(calls, 1)  # Retrying would repeat text the customer already saw.


class FallbackOrderCodeTest(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_reads_orders_of_a_shop_with_its_own_code_format(self):
        adapter = RestCommerceAdapter(SECOND_CONFIG, "test-second-key", "http://localhost:8003",
                                      transport=httpx.ASGITransport(app=second_shop))
        ctx = TurnContext(shop={"shop_id": "second_shop", "shop_url": "http://localhost:8003"},
                          customer_id="K1001", conversation_id="c", turn_key="t", adapter=adapter,
                          search=lambda *args: [])
        status, reply, sources = await fallback.answer(ctx, "đơn pkx204518 tới đâu rồi")
        self.assertIn("S22.81726354", reply)
        self.assertEqual(sources[0]["href"], "http://localhost:8003/account/orders/PKX204518")


if __name__ == "__main__":
    unittest.main()
