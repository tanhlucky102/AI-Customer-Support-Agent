"""Trang chủ (lọc, sắp xếp), chi tiết sản phẩm, yêu thích và trung tâm trợ giúp."""
import sqlite3

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from db import get_db
import help_pages
from web import PRICE_RANGES, SORTS, STEP, back, current_user, flash, render, to_login

router = APIRouter()


@router.get("/")
def home(request: Request, q: str = "", category: str = "", brand: list[str] = Query([]), price: str = "",
         os: str = "", storage: int = 0, sort: str = "popular", show: int = Query(STEP, ge=STEP, le=400),
         db: sqlite3.Connection = Depends(get_db)):
    where, args = ["1=1"], []
    if q:
        where.append("(name LIKE ? OR brand LIKE ?)")
        args += [f"%{q}%"] * 2
    if category:
        where.append("category = ?")
        args.append(category)
    if brand:
        where.append(f"brand IN ({','.join('?' * len(brand))})")
        args += brand
    if price in PRICE_RANGES:
        where.append("price >= ? AND price < ?")
        args += list(PRICE_RANGES[price][1:])
    if os in ("iOS", "Android"):
        where.append("os = ?")
        args.append(os)
    if storage:
        where.append("storage_gb = ?")
        args.append(storage)
    order = SORTS.get(sort, SORTS["popular"])[1]
    cond = " AND ".join(where)
    total = db.execute(f"SELECT COUNT(*) FROM products WHERE {cond}", args).fetchone()[0]
    products = db.execute(f"SELECT * FROM products WHERE {cond} ORDER BY {order}, id LIMIT ?",
                          args + [show]).fetchall()
    brands = [r[0] for r in db.execute("SELECT brand FROM products GROUP BY brand ORDER BY COUNT(*) DESC")]
    categories = [r[0] for r in db.execute(
        "SELECT category FROM products GROUP BY category ORDER BY category = 'Điện thoại' DESC, category")]
    filtered = bool(q or category or brand or price or os or storage)
    return render(request, db, "index.html", products=products, categories=categories, brands=brands, q=q,
                  category=category, sel_brands=brand, price=price, os=os, storage=storage, sort=sort,
                  show=show, total=total, more=max(0, total - len(products)), filtered=filtered, step=STEP)


@router.get("/products/{product_id}")
def product_detail(request: Request, product_id: int, db: sqlite3.Connection = Depends(get_db)):
    p = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not p:
        raise HTTPException(404, "Không tìm thấy sản phẩm")
    return render(request, db, "product.html", p=p)


@router.post("/wishlist/toggle")
def wishlist_toggle(request: Request, product_id: int = Form(...), next: str = Form("/"),
                    db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    if db.execute("DELETE FROM wishlist WHERE customer_id = ? AND product_id = ?", (user["id"], product_id)).rowcount:
        flash(request, "Đã bỏ khỏi danh sách yêu thích.")
    elif db.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone():
        db.execute("INSERT INTO wishlist(customer_id, product_id) VALUES (?, ?)", (user["id"], product_id))
        flash(request, "Đã thêm vào danh sách yêu thích ❤️")
    return back(next)


@router.get("/wishlist")
def wishlist_page(request: Request, db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    products = db.execute("SELECT p.* FROM products p JOIN wishlist w ON w.product_id = p.id "
                          "WHERE w.customer_id = ? ORDER BY p.id DESC", (user["id"],)).fetchall()
    return render(request, db, "wishlist.html", products=products)


@router.get("/policy/{slug}")
def policy(slug: str):
    """Old policy links now open the help-centre page with the same slug."""
    return RedirectResponse(f"/help/{slug}" if help_pages.page(slug) else "/help", status_code=301)


@router.get("/help")
def help_index(request: Request, db: sqlite3.Connection = Depends(get_db)):
    return render(request, db, "help.html", title="Trung tâm trợ giúp", doc=None, groups=help_pages.index())


@router.get("/help/{slug}")
def help_page(request: Request, slug: str, db: sqlite3.Connection = Depends(get_db)):
    doc = help_pages.page(slug)
    if not doc:
        raise HTTPException(404, "Không tìm thấy trang")
    return render(request, db, "help.html", title=doc["title"], doc=doc, groups=help_pages.index())
