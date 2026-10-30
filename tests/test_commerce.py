"""CommerceAdapter: the standard profile (contract C2) and a declarative mapping of a foreign API."""

import json
import os
import unittest

os.environ["SECOND_SHOP_API_KEY"] = "test-second-key"

from tests import support  # noqa: E402,F401

import httpx  # noqa: E402

from agent_server.commerce import CommerceError, RestCommerceAdapter, adapter_for  # noqa: E402
from agent_server.tools import toolset_for  # noqa: E402
from examples.second_shop.app import app as second_shop  # noqa: E402

SECOND_CONFIG = json.loads((support.ROOT / "examples" / "second_shop" / "commerce.json")
                           .read_text(encoding="utf-8"))


class SecondShopMappingTest(unittest.IsolatedAsyncioTestCase):
    def adapter(self, key="test-second-key") -> RestCommerceAdapter:
        return RestCommerceAdapter(SECOND_CONFIG, key, "http://localhost:8003",
                                   transport=httpx.ASGITransport(app=second_shop))

    async def test_foreign_order_is_mapped_to_canonical_fields_and_status(self):
        order = await self.adapter().get_order("PKX204518", "K1001")
        self.assertEqual(order["id"], "PKX204518")
        self.assertEqual(order["status"], "shipping")
        self.assertEqual(order["status_label"], "Đang giao")
        self.assertEqual(order["tracking_code"], "S22.81726354")
        self.assertEqual(order["items"][0], {"name": "Sạc nhanh GaN 65W 3 cổng", "qty": 1,
                                             "unit_price": 590000})
        self.assertEqual(order["total"], 590000)
        self.assertEqual(order["url"], "http://localhost:8003/account/orders/PKX204518")

    async def test_other_customers_order_is_not_found(self):
        self.assertIsNone(await self.adapter().get_order("PKX204518", "K1002"))

    async def test_list_reuses_order_mapping_and_translates_status_filter(self):
        orders = await self.adapter().list_orders("K1001", 5, "delivered")
        self.assertEqual([order["id"] for order in orders], ["PKX203977"])
        self.assertEqual(len(await self.adapter().list_orders("K1001", 5, None)), 3)

    async def test_customer_fields_are_mapped(self):
        customer = await self.adapter().get_customer("K1001")
        self.assertEqual(customer["full_name"], "Lê Minh Châu")
        self.assertEqual(customer["email"], "chau@pkx.vn")

    async def test_wrong_key_is_an_error_not_an_empty_answer(self):
        with self.assertRaises(CommerceError):
            await self.adapter("wrong").get_order("PKX204518", "K1001")

    def test_capabilities_decide_the_toolset(self):
        names = {tool.name for tool in toolset_for(self.adapter())}
        self.assertIn("get_order_status", names)
        self.assertNotIn("search_products", names)  # The second shop has no catalogue API.


class StandardProfileTest(unittest.IsolatedAsyncioTestCase):
    async def test_path_variables_are_encoded_and_only_get_is_used(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(404)

        shop = {"shop_id": "demo_shop", "shop_url": "http://localhost:8001",
                "commerce_config": {"profile": "standard", "base_url": "http://localhost:8001"},
                "commerce_api_key": "k"}
        adapter = adapter_for(shop, httpx.MockTransport(handler))
        self.assertEqual(adapter.capabilities, frozenset(
            {"get_order", "list_orders", "get_customer", "search_products"}))
        self.assertIsNone(await adapter.get_order("../../admin", "C1/../C2"))
        self.assertEqual(seen[0].method, "GET")
        self.assertEqual(seen[0].url.raw_path.decode(),
                         "/api/v1/customers/C1%2F..%2FC2/orders/..%2F..%2Fadmin")
        self.assertEqual(seen[0].headers["authorization"], "Bearer k")

    async def test_server_error_raises(self):
        shop = {"shop_id": "s", "shop_url": "http://x", "commerce_api_key": "k",
                "commerce_config": {"profile": "standard", "base_url": "http://x"}}
        adapter = adapter_for(shop, httpx.MockTransport(lambda request: httpx.Response(503)))
        with self.assertRaises(CommerceError):
            await adapter.list_orders("C1", 5, None)

    def test_shop_without_commerce_config_is_level_zero(self):
        self.assertIsNone(adapter_for({"shop_id": "s", "shop_url": "http://x", "commerce_config": None}))
        names = {tool.name for tool in toolset_for(None)}
        self.assertEqual(names, {"search_knowledge_base", "get_promotions", "escalate_to_human"})


if __name__ == "__main__":
    unittest.main()
