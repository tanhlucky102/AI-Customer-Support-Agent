"""REST API đơn hàng cho Agent Server (hợp đồng C2). Server-to-server, bảo vệ bằng Bearer API key.

Phòng thủ nhiều lớp: dù Agent Server có bị lừa, API vẫn chỉ trả đơn thuộc đúng customer_id trong path.
Đơn không tồn tại và đơn của người khác đều trả 404 giống nhau (không lộ sự tồn tại của đơn).
"""
import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from config import API_KEY
from db import get_db
from security import keys_match

router = APIRouter(prefix="/api/v1")


def require_api_key(authorization: str | None = Header(default=None)) -> None:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token or not keys_match(token, API_KEY):
        raise HTTPException(401, "invalid_api_key", headers={"WWW-Authenticate": "Bearer"})


def _items(db: sqlite3.Connection, order_id: str) -> list[dict]:
    rows = db.execute(
        "SELECT p.id AS product_id, p.sku, p.name, i.qty, i.unit_price FROM order_items i "
        "JOIN products p ON p.id = i.product_id WHERE i.order_id = ?", (order_id,)).fetchall()
    return [dict(r) for r in rows]


def order_json(db: sqlite3.Connection, row: sqlite3.Row, with_items: bool = True) -> dict:
    d = {k: row[k] for k in ("id", "customer_id", "status", "total", "shipping_fee", "payment_method",
                              "placed_at", "eta", "tracking_code", "ship_name", "ship_address", "discount",
                              "coupon_code")}
    if with_items:
        d["items"] = _items(db, row["id"])
    return d


@router.get("/customers/{customer_id}", dependencies=[Depends(require_api_key)])
def get_customer(customer_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT id, email, full_name, phone, address, tier, created_at FROM customers WHERE id = ?",
                     (customer_id,)).fetchone()
    if not row:
        raise HTTPException(404, "customer_not_found")
    return dict(row)


@router.get("/customers/{customer_id}/orders", dependencies=[Depends(require_api_key)])
def list_orders(customer_id: str, limit: int = Query(10, ge=1, le=50), status: str | None = None,
                db: sqlite3.Connection = Depends(get_db)):
    sql, args = "SELECT * FROM orders WHERE customer_id = ?", [customer_id]
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY placed_at DESC LIMIT ?"
    args.append(limit)
    return [order_json(db, r, with_items=False) for r in db.execute(sql, args)]


@router.get("/customers/{customer_id}/orders/{order_id}", dependencies=[Depends(require_api_key)])
def get_order(customer_id: str, order_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM orders WHERE id = ? AND customer_id = ?",
                     (order_id.upper(), customer_id)).fetchone()
    if not row:
        raise HTTPException(404, "order_not_found")
    return order_json(db, row)
