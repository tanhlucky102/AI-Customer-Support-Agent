"""The agent's tools: JSON schemas for the model, Pydantic re-validation, and execution.

The model never supplies a customer ID: every commerce call uses the ID from the verified chat
token (TurnContext.customer_id). Arguments are validated again on the server, and unknown fields
are rejected, so a prompt-injected "customer_id" argument fails validation instead of being used.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_server import store
from agent_server.commerce import ORDER_STATUSES, CommerceAdapter, CommerceError
from rag.documents import CATEGORIES
from shared.pii import mask_address, mask_email, mask_phone


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchArgs(Args):
    query: str = Field(min_length=2, max_length=300)
    category: Literal[CATEGORIES] | None = None  # type: ignore[valid-type]


class OrderArgs(Args):
    order_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")


class ListOrdersArgs(Args):
    limit: int = Field(default=5, ge=1, le=10)
    status: Literal[ORDER_STATUSES] | None = None  # type: ignore[valid-type]


class NoArgs(Args):
    pass


class ProductArgs(Args):
    query: str = Field(default="", max_length=120)
    category: str | None = Field(default=None, max_length=60)
    max_price: int | None = Field(default=None, ge=0, le=1_000_000_000)


class PromotionArgs(Args):
    kind: Literal["promotion", "event", "all"] = "all"


class EscalateArgs(Args):
    reason: Literal["customer_request", "complaint", "needs_staff_action", "unanswered"]
    transcript_summary: str = Field(min_length=5, max_length=1000)


@dataclass
class TurnContext:
    shop: dict
    customer_id: str
    conversation_id: str
    turn_key: str
    adapter: CommerceAdapter | None
    search: Callable[..., list[dict]]
    sources: dict[int, dict] = field(default_factory=dict)  # citation number -> {label, href}
    links: list[dict] = field(default_factory=list)          # order / product / promotion pages

    def cite(self, label: str, href: str | None) -> int:
        number = len(self.sources) + 1
        self.sources[number] = {"label": label, "href": href}
        return number


@dataclass
class Outcome:
    content: dict
    ok: bool = True
    final_text: str | None = None  # Set when the tool ends the turn (escalation).


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[Args]
    status: str
    capability: str | None
    run: Callable


def schema(model: type[Args]) -> dict:
    result = model.model_json_schema()
    result.pop("title", None)
    for prop in result.get("properties", {}).values():
        prop.pop("title", None)
        if "anyOf" in prop:  # Optional[...] -> plain type, and null is allowed by omission.
            options = [item for item in prop.pop("anyOf") if item.get("type") != "null"]
            prop.update(options[0])
        if prop.get("default", 0) is None:
            del prop["default"]
    result["additionalProperties"] = False
    return result


async def search_knowledge_base(ctx: TurnContext, args: SearchArgs) -> Outcome:
    hits = await asyncio.to_thread(ctx.search, args.query, ctx.shop["shop_id"], args.category)
    if not hits:
        return Outcome({"no_results": True,
                        "note": "Nothing in this shop's documents answers this. Do not guess."})
    results = []
    for hit in hits:
        number = ctx.cite(f"{hit['doc_title']} – {hit['section']}", hit.get("url"))
        results.append({"ref": number, "document": hit["doc_title"], "section": hit["section"],
                        "text": hit["content"].split("\n", 1)[-1]})
    return Outcome({"results": results})


def _order_for_model(order: dict) -> dict:
    keep = ("id", "status", "status_label", "placed_at", "eta", "tracking_code", "total",
            "shipping_fee", "discount", "payment_method", "items")
    return {key: order[key] for key in keep if order.get(key) not in (None, [], "")}


def _link(ctx: TurnContext, label: str, href: str | None) -> None:
    if href and all(link["href"] != href for link in ctx.links):
        ctx.links.append({"label": label, "href": href})


async def get_order_status(ctx: TurnContext, args: OrderArgs) -> Outcome:
    order = await ctx.adapter.get_order(args.order_id.upper(), ctx.customer_id)
    if order is None:
        return Outcome({"not_found": True, "order_id": args.order_id.upper(),
                        "note": "No such order in this customer's account."})
    _link(ctx, f"Xem đơn {order['id']}", order.get("url"))
    return Outcome({"order": _order_for_model(order)})


async def list_customer_orders(ctx: TurnContext, args: ListOrdersArgs) -> Outcome:
    orders = await ctx.adapter.list_orders(ctx.customer_id, args.limit, args.status)
    if ctx.adapter and orders:
        orders_page = ctx.shop["shop_url"].rstrip("/") + "/orders"
        _link(ctx, "Đơn hàng của tôi", orders_page if ctx.shop["shop_id"] == "demo_shop"
              else orders[0].get("url"))
    return Outcome({"orders": [_order_for_model({**order, "items": None}) for order in orders],
                    "count": len(orders)})


async def get_customer_profile(ctx: TurnContext, args: NoArgs) -> Outcome:
    customer = await ctx.adapter.get_customer(ctx.customer_id)
    if customer is None:
        return Outcome({"not_found": True})
    return Outcome({"customer": {
        "full_name": customer.get("full_name"), "tier": customer.get("tier"),
        "email": mask_email(customer.get("email")), "phone": mask_phone(customer.get("phone")),
        "address": mask_address(customer.get("address")), "member_since": customer.get("created_at"),
    }})


async def search_products(ctx: TurnContext, args: ProductArgs) -> Outcome:
    products = await ctx.adapter.search_products(args.query, args.category, args.max_price, 5)
    for product in products[:3]:
        _link(ctx, f"Xem {product['name']}", product.get("url"))
    return Outcome({"products": [{key: value for key, value in product.items()
                                  if key != "url" and value is not None} for product in products],
                    "count": len(products)})


async def get_promotions(ctx: TurnContext, args: PromotionArgs) -> Outcome:
    snapshot = store.list_offers(ctx.shop["shop_id"])
    if not snapshot["revision"]:
        return Outcome({"published": False,
                        "note": "This shop has not published promotions to the assistant."})
    now = datetime.now(timezone.utc)
    current, upcoming = [], []
    for offer in snapshot["offers"]:
        if args.kind != "all" and offer["kind"] != args.kind:
            continue
        start = datetime.fromisoformat(offer["starts_at"]) if offer["starts_at"] else None
        end = datetime.fromisoformat(offer["ends_at"]) if offer["ends_at"] else None
        if end and end < now:
            continue
        item = {key: offer[key] for key in ("kind", "title", "description", "starts_at", "ends_at")
                if offer[key]}
        (upcoming if start and start > now else current).append(item)
        _link(ctx, offer["title"], offer.get("source_url"))
    return Outcome({"current": current, "upcoming": upcoming,
                    "published_at": snapshot["updated_at"]})


async def escalate_to_human(ctx: TurnContext, args: EscalateArgs) -> Outcome:
    code = await asyncio.to_thread(
        store.create_ticket, conversation_id=ctx.conversation_id, shop_id=ctx.shop["shop_id"],
        customer_id=ctx.customer_id, reason=args.reason, summary=args.transcript_summary,
        idempotency_key=ctx.turn_key)
    return Outcome({"ticket": code}, final_text=(
        f"Mình đã chuyển yêu cầu của bạn cho nhân viên hỗ trợ (mã phiếu {code}). "
        "Nhân viên sẽ liên hệ với bạn qua email trong vòng 24 giờ."))


TOOLS = [
    Tool("search_knowledge_base",
         "Search this shop's help-center documents: policies, fees, returns, warranty, shipping, "
         "payment, installment, membership, account, store and contact information, and product "
         "how-to guides. Use it for every question whose answer is a rule, fee, procedure or "
         "instruction rather than the customer's own data. `query` must be a complete, "
         "self-contained question in Vietnamese that resolves references from earlier turns "
         "(e.g. 'thời hạn đổi trả điện thoại lỗi', not 'còn cái kia thì sao'). Results are "
         "numbered; cite them as [n]. `no_results` means the documents do not cover it.",
         SearchArgs, "Đang tra cứu tài liệu của shop...", None, search_knowledge_base),
    Tool("get_order_status",
         "Get one of the logged-in customer's own orders by its order ID (e.g. SO10293): status, "
         "estimated delivery date, tracking code, total and items. `not_found` means that ID is "
         "not an order of this customer.",
         OrderArgs, "Đang kiểm tra đơn hàng...", "get_order", get_order_status),
    Tool("list_customer_orders",
         "List the logged-in customer's most recent orders, newest first, optionally filtered by "
         "status. Use it when they ask about their orders without giving an ID.",
         ListOrdersArgs, "Đang xem các đơn hàng gần đây...", "list_orders", list_customer_orders),
    Tool("get_customer_profile",
         "Get the logged-in customer's account summary: name, membership tier, member since, and "
         "masked e-mail, phone and address.",
         NoArgs, "Đang xem thông tin tài khoản...", "get_customer", get_customer_profile),
    Tool("search_products",
         "Search this shop's live product catalogue for current price, old price, stock and basic "
         "specs. `query` is a product name, brand or SKU; `max_price` is in VND.",
         ProductArgs, "Đang tìm sản phẩm...", "search_products", search_products),
    Tool("get_promotions",
         "Get the promotions and dated events this shop has currently published, with dates.",
         PromotionArgs, "Đang xem ưu đãi của shop...", None, get_promotions),
    Tool("escalate_to_human",
         "Open a support ticket so that a human agent takes over. Use it when the customer asks "
         "for a person, makes a complaint, needs something only staff can do (refund, cancelling "
         "or changing an order that is already shipping, changing the delivery address), or still "
         "needs help after the documents had no answer. Do not use it for questions the other "
         "tools can answer. Staff reply by e-mail within 24 hours.",
         EscalateArgs, "Đang chuyển cho nhân viên hỗ trợ...", None, escalate_to_human),
]
BY_NAME = {tool.name: tool for tool in TOOLS}


def toolset_for(adapter: CommerceAdapter | None) -> list[Tool]:
    """Offer only the tools the shop's integration supports (level 0 has no commerce tools)."""
    capabilities = adapter.capabilities if adapter else frozenset()
    return [tool for tool in TOOLS if tool.capability is None or tool.capability in capabilities]


def definitions(tools: list[Tool]) -> list[dict]:
    return [{"type": "function", "function": {"name": tool.name, "description": tool.description,
                                              "parameters": schema(tool.args)}} for tool in tools]


async def execute(ctx: TurnContext, tools: list[Tool], name: str, raw_arguments: str) -> tuple[dict, Outcome]:
    """Validate and run one tool call; failures become data the model can explain, never crashes."""
    tool = next((item for item in tools if item.name == name), None)
    if tool is None:
        return {}, Outcome({"error": "unknown_tool", "tool": name}, ok=False)
    try:
        raw = json.loads(raw_arguments or "{}")
        if not isinstance(raw, dict):
            raise ValueError("arguments must be an object")
        # Models sometimes send null for optional fields; treat it as omitted.
        raw = {key: value for key, value in raw.items() if value is not None}
        args = tool.args.model_validate(raw)
    except (ValueError, ValidationError) as error:
        return {"raw": str(raw_arguments)[:300]}, Outcome(
            {"error": "invalid_arguments", "details": str(error)[:300]}, ok=False)
    try:
        outcome = await tool.run(ctx, args)
    except CommerceError:
        outcome = Outcome({"error": "shop_system_unavailable",
                           "note": "The shop's order system did not respond. Apologise and offer "
                                   "to hand over to staff; do not guess."}, ok=False)
    return args.model_dump(exclude_none=True), outcome
