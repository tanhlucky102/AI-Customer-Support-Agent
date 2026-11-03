"""CommerceAdapter: how the agent reads a shop's live data (orders, customer, products).

Shops differ in what they can offer, so integration has levels:
  0. No commerce API: the shop pushes FAQ/offers and documents; the agent gets no commerce tools.
  1. Commerce API: the shop gives a base URL, a key and a declarative mapping (this module's
     RestCommerceAdapter). A shop that implements our contract C2 uses the "standard" profile with
     no mapping at all; a shop with its own API describes paths and field names in JSON, no code.
Each operation the mapping declares becomes a capability, and the agent is offered only the tools
whose capability exists (see tools.toolset_for).

Safety: the base URL is set by the operator at provisioning, never by the shop at runtime or by the
model; path variables are URL-encoded; only GET is used; redirects are not followed; responses are
size-capped; customer_id always comes from the verified chat token.
"""

import copy
from typing import Protocol
from urllib.parse import quote

import httpx

CAPABILITIES = ("get_order", "list_orders", "get_customer", "search_products")
# Operations that return one customer's data must be scoped to the token's customer: either the
# request names {customer_id} (the shop filters), or the mapping names an owner field that the
# adapter checks on every record (for admin-style APIs that return any customer's order).
CUSTOMER_SCOPED = ("get_order", "list_orders", "get_customer")
ORDER_STATUSES = ("pending", "confirmed", "shipping", "delivered", "cancelled", "returned")
STATUS_LABEL = {
    "pending": "Chờ xác nhận", "confirmed": "Đã xác nhận", "shipping": "Đang giao",
    "delivered": "Đã giao", "cancelled": "Đã huỷ", "returned": "Đã hoàn trả", "unknown": "Không rõ",
}
MAX_RESPONSE_BYTES = 1_000_000

# Contract C2 (the demo shop): canonical paths and field names, so the mapping is the identity.
STANDARD_PROFILE = {
    "auth": {"type": "bearer"},
    "timeout_seconds": 6,
    "operations": {
        "get_order": {"path": "/api/v1/customers/{customer_id}/orders/{order_id}"},
        "list_orders": {"path": "/api/v1/customers/{customer_id}/orders",
                        "params": {"limit": "{limit}", "status": "{status}"}},
        "get_customer": {"path": "/api/v1/customers/{customer_id}"},
        "search_products": {"path": "/api/v1/products",
                            "params": {"q": "{query}", "category": "{category}",
                                       "max_price": "{max_price}", "limit": "{limit}"}},
    },
    "links": {"order": "/orders/{order_id}", "product": "/products/{product_id}"},
}

ORDER_FIELDS = ("id", "status", "total", "shipping_fee", "discount", "payment_method", "placed_at",
                "eta", "tracking_code")
ITEM_FIELDS = ("name", "qty", "unit_price")
CUSTOMER_FIELDS = ("id", "full_name", "email", "phone", "address", "tier", "created_at")
PRODUCT_FIELDS = ("id", "name", "brand", "category", "price", "old_price", "stock", "os", "ram_gb",
                  "storage_gb", "screen", "rating")


class CommerceError(Exception):
    """The shop API failed (network, auth, 5xx, malformed data); not the same as 'not found'."""


class CommerceAdapter(Protocol):
    capabilities: frozenset[str]

    async def get_order(self, order_id: str, customer_id: str) -> dict | None: ...
    async def list_orders(self, customer_id: str, limit: int, status: str | None) -> list[dict]: ...
    async def get_customer(self, customer_id: str) -> dict | None: ...
    async def search_products(self, query: str, category: str | None, max_price: int | None,
                              limit: int) -> list[dict]: ...


def pick(data, path: str | None):
    """Read a dotted path ('amount.value', 'data.0.id'); a missing key yields None."""
    if not path:
        return data
    for part in path.split("."):
        if isinstance(data, dict):
            data = data.get(part)
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            return None
    return data


def resolve_config(config: dict) -> dict:
    """Merge a shop's stored config over its profile."""
    merged = copy.deepcopy(STANDARD_PROFILE) if config.get("profile") == "standard" else {
        "auth": {"type": "bearer"}, "timeout_seconds": 6, "operations": {}, "links": {}}
    for key, value in config.items():
        if key == "operations":
            for name, operation in value.items():
                merged["operations"][name] = {**merged["operations"].get(name, {}), **operation}
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def _scoped(operations: dict, name: str) -> bool:
    spec = operations.get(name) or {}
    if name == "list_orders" and not spec.get("owner_field"):
        spec = {**spec, "owner_field": (operations.get("get_order") or {}).get("owner_field")}
    templates = [spec.get("path", ""), *(spec.get("params") or {}).values()]
    return bool(spec.get("owner_field")) or any("{customer_id}" in str(t) for t in templates)


def config_problems(config: dict) -> list[str]:
    """What makes a mapping unsafe; checked at provisioning and again whenever it is loaded."""
    operations = resolve_config(config)["operations"]
    problems = []
    for name, spec in operations.items():
        path = str(spec.get("path", ""))
        if not path.startswith("/") or path.startswith("//"):
            problems.append(f"{name}: path must be relative to base_url and start with '/'")
    for name in CUSTOMER_SCOPED:
        if name in operations and not _scoped(operations, name):
            problems.append(f"{name}: not scoped to the customer; use {{customer_id}} in the path "
                            "or params, or name an owner_field")
    return problems


class RestCommerceAdapter:
    def __init__(self, config: dict, api_key: str | None, shop_url: str,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.config = resolve_config(config)
        self.base_url = self.config["base_url"].rstrip("/")
        self.api_key = api_key or ""
        self.shop_url = shop_url.rstrip("/")
        self.transport = transport
        self.status_map = self.config.get("status_map") or {}
        unsafe = {problem.split(":", 1)[0] for problem in config_problems(config)}
        # An unsafe operation is never offered to the model, even if such a config was stored.
        self.capabilities = frozenset(name for name in CAPABILITIES
                                      if name in self.config["operations"] and name not in unsafe)

    def _headers(self) -> dict:
        auth = self.config.get("auth") or {}
        if auth.get("type") == "header":
            return {auth.get("name", "X-Api-Key"): self.api_key}
        return {"Authorization": f"Bearer {self.api_key}"}

    async def _get(self, operation: str, variables: dict):
        if operation not in self.capabilities:
            raise CommerceError(f"{operation}: not available for this shop")
        spec = self.config["operations"][operation]
        encoded = {name: quote(str(value), safe="") for name, value in variables.items()
                   if value is not None}
        try:
            path = spec["path"].format(**encoded)
        except KeyError as error:
            raise CommerceError(f"mapping for {operation} uses unknown variable {error}") from None
        params = {}
        for name, template in (spec.get("params") or {}).items():
            key = template.strip("{}") if template.startswith("{") else None
            value = variables.get(key) if key else template
            if value is not None and value != "":
                params[name] = self._status_out(value) if key == "status" else value
        try:
            async with httpx.AsyncClient(base_url=self.base_url, transport=self.transport,
                                         timeout=float(self.config.get("timeout_seconds", 6)),
                                         follow_redirects=False) as client:
                response = await client.get(path, params=params, headers=self._headers())
        except httpx.HTTPError as error:
            raise CommerceError(f"{operation}: {type(error).__name__}") from None
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise CommerceError(f"{operation}: HTTP {response.status_code}")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise CommerceError(f"{operation}: response too large")
        try:
            data = response.json()
        except ValueError:
            raise CommerceError(f"{operation}: invalid JSON") from None
        result = pick(data, spec.get("result"))
        if result is None:  # A 200 without the expected record is an error, not "not found".
            raise CommerceError(f"{operation}: response has no {spec.get('result')!r}")
        return result

    def _status_in(self, value) -> str:
        value = self.status_map.get(str(value), value)
        return value if value in ORDER_STATUSES else "unknown"

    def _status_values(self, canonical: str) -> list[str]:
        return [theirs for theirs, ours in self.status_map.items() if ours == canonical] or [canonical]

    def _status_out(self, canonical: str) -> str:
        return self._status_values(canonical)[0]

    def _owned(self, operation: str, record: dict, customer_id: str) -> bool:
        owner_field = self._spec(operation, "owner_field")
        return not owner_field or str(pick(record, owner_field)) == str(customer_id)

    def _spec(self, operation: str, key: str):
        """list_orders reuses get_order's field mapping unless it declares its own."""
        operations = self.config["operations"]
        value = operations[operation].get(key)
        if value is None and operation == "list_orders":
            value = operations.get("get_order", {}).get(key)
        return value

    def _map(self, operation: str, record: dict, fields: tuple[str, ...]) -> dict:
        mapping = self._spec(operation, "fields") or {}
        return {name: pick(record, mapping.get(name, name)) for name in fields}

    def _link(self, kind: str, **values) -> str | None:
        template = (self.config.get("links") or {}).get(kind)
        if not template or any(value is None for value in values.values()):
            return None
        try:
            return self.shop_url + template.format(**{k: quote(str(v), safe="") for k, v in values.items()})
        except (KeyError, IndexError, ValueError):
            return None  # A broken link template must not break the answer.

    def _order(self, operation: str, record) -> dict:
        if not isinstance(record, dict):
            raise CommerceError(f"{operation}: order is not an object")
        order = self._map(operation, record, ORDER_FIELDS)
        if not order["id"]:
            raise CommerceError(f"{operation}: order without id")
        order["status"] = self._status_in(order["status"])
        order["status_label"] = STATUS_LABEL[order["status"]]
        items_spec = self._spec(operation, "items")
        raw_items = pick(record, (items_spec or {}).get("path", "items"))
        if isinstance(raw_items, list):
            item_fields = (items_spec or {}).get("fields") or {}
            order["items"] = [{name: pick(item, item_fields.get(name, name)) for name in ITEM_FIELDS}
                              for item in raw_items[:20] if isinstance(item, dict)]
        order["url"] = self._link("order", order_id=order["id"])
        return order

    async def get_order(self, order_id: str, customer_id: str) -> dict | None:
        record = await self._get("get_order", {"order_id": order_id, "customer_id": customer_id})
        if record is None:
            return None
        if isinstance(record, dict) and not self._owned("get_order", record, customer_id):
            return None  # Another customer's order looks exactly like a missing one.
        return self._order("get_order", record)

    async def list_orders(self, customer_id: str, limit: int = 5,
                          status: str | None = None) -> list[dict]:
        params = self.config["operations"]["list_orders"].get("params") or {}
        # The shop filters only when exactly one of its statuses maps to the requested one;
        # otherwise fetch more and filter here, so older matching orders are not cut off.
        remote = status is None or ("{status}" in params.values()
                                    and len(self._status_values(status)) == 1)
        records = await self._get("list_orders", {"customer_id": customer_id,
                                                  "limit": limit if remote else 50,
                                                  "status": status if remote else None})
        if records is None:
            return []
        if not isinstance(records, list):
            raise CommerceError("list_orders: expected a list")
        orders = [self._order("list_orders", record) for record in records
                  if isinstance(record, dict) and self._owned("list_orders", record, customer_id)]
        return [order for order in orders if status is None or order["status"] == status][:limit]

    async def get_customer(self, customer_id: str) -> dict | None:
        record = await self._get("get_customer", {"customer_id": customer_id})
        if record is None:
            return None
        if not isinstance(record, dict):
            raise CommerceError("get_customer: expected an object")
        if not self._owned("get_customer", record, customer_id):
            return None
        return self._map("get_customer", record, CUSTOMER_FIELDS)

    async def search_products(self, query: str, category: str | None = None,
                              max_price: int | None = None, limit: int = 5) -> list[dict]:
        records = await self._get("search_products", {"query": query, "category": category,
                                                      "max_price": max_price, "limit": limit})
        if records is None:
            return []
        if not isinstance(records, list):
            raise CommerceError("search_products: expected a list")
        products = []
        for record in records[:limit]:
            if isinstance(record, dict):
                product = self._map("search_products", record, PRODUCT_FIELDS)
                product["url"] = self._link("product", product_id=product["id"])
                products.append(product)
        return products


def adapter_for(shop: dict, transport: httpx.AsyncBaseTransport | None = None) -> RestCommerceAdapter | None:
    """None means integration level 0: the shop has connected no commerce API."""
    config = shop.get("commerce_config")
    if not config or not config.get("base_url"):
        return None
    return RestCommerceAdapter(config, shop.get("commerce_api_key"), shop["shop_url"], transport)
