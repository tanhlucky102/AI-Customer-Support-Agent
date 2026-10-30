"""Help-centre pages rendered from the shop's Markdown documents (data/kb/demo_shop).

These are the pages the chatbot cites, so every citation link opens the exact section it quotes
(each '## ' heading gets an anchor #muc-<n> in document order).
"""

import html
import re
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent.parent / "data" / "kb" / "demo_shop"
FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
CATEGORY_LABEL = {
    "returns": "Đổi trả & hoàn tiền", "warranty": "Bảo hành", "shipping": "Giao hàng",
    "payment": "Thanh toán", "orders": "Đơn hàng", "account": "Tài khoản & bảo mật",
    "membership": "Thành viên", "promotions": "Khuyến mãi", "store": "Cửa hàng & hỗ trợ",
    "product_guide": "Hướng dẫn sử dụng",
}


def _inline(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(text))


def _meta(text: str) -> tuple[dict, str]:
    match = FRONT_MATTER.match(text)
    if not match:
        return {}, text
    meta = dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    return {key.strip(): value.strip() for key, value in meta.items()}, text[match.end():]


def render(markdown: str) -> str:
    out, lines, index, section = [], markdown.splitlines(), 0, 0
    while index < len(lines):
        line = lines[index].rstrip()
        if line.startswith("# "):
            index += 1
            continue  # The page template shows the title.
        if line.startswith("## "):
            out.append(f'<h2 id="muc-{section}">{_inline(line[3:])}</h2>')
            section += 1
        elif line.startswith("|"):
            rows = []
            while index < len(lines) and lines[index].startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if not all(set(cell) <= set("-: ") for cell in cells):
                    rows.append(cells)
                index += 1
            head, *body = rows
            out.append("<table><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>"
                                 for row in body) + "</table>")
            continue
        elif re.match(r"^(\s*[-*]|\s*\d+\.)\s+", line):
            ordered = bool(re.match(r"^\s*\d+\.", line))
            items = []
            while index < len(lines) and re.match(r"^(\s*[-*]|\s*\d+\.)\s+", lines[index]):
                items.append(re.sub(r"^(\s*[-*]|\s*\d+\.)\s+", "", lines[index]))
                index += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(item)}</li>" for item in items) + f"</{tag}>")
            continue
        elif line.strip():
            out.append(f"<p>{_inline(line)}</p>")
        index += 1
    return "\n".join(out)


def page(slug: str) -> dict | None:
    path = KB_DIR / f"{slug}.md"
    if not re.fullmatch(r"[a-z0-9-]+", slug) or not path.exists():
        return None
    meta, body = _meta(path.read_text(encoding="utf-8"))
    return {"slug": slug, "title": meta.get("title", slug), "category": meta.get("category", ""),
            "updated_at": meta.get("updated_at", ""), "html": render(body)}


def index() -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for path in sorted(KB_DIR.glob("*.md")):
        meta, _ = _meta(path.read_text(encoding="utf-8"))
        label = CATEGORY_LABEL.get(meta.get("category", ""), "Khác")
        groups.setdefault(label, []).append({"slug": path.stem, "title": meta.get("title", path.stem)})
    return dict(sorted(groups.items()))
