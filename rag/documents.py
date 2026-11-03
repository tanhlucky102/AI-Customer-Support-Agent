"""Parse knowledge-base Markdown files: front matter, one '# ' title, '## ' sections."""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

CATEGORIES = ("returns", "warranty", "shipping", "payment", "orders", "account", "membership",
              "promotions", "product_guide", "store", "faq")
FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Section:
    position: int
    heading: str
    content: str


@dataclass
class Document:
    slug: str
    title: str
    category: str
    updated_at: date | None
    intro: str
    sections: list[Section] = field(default_factory=list)
    source_url: str | None = None
    origin: str = "file"

    @property
    def text(self) -> str:
        parts = [f"# {self.title}", self.intro]
        parts += [f"## {section.heading}\n{section.content}" for section in self.sections]
        return "\n\n".join(part for part in parts if part)

    def fingerprint(self, *settings: str) -> str:
        return hashlib.sha256("\x1f".join([self.text, self.category, *settings]).encode()).hexdigest()


def parse(text: str, slug: str) -> Document:
    match = FRONT_MATTER.match(text)
    if not match:
        raise ValueError(f"{slug}: missing front matter")
    meta = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        if key.strip():
            meta[key.strip()] = value.strip()
    body = text[match.end():]
    if meta.get("category") not in CATEGORIES:
        raise ValueError(f"{slug}: unknown category {meta.get('category')!r}")
    title, intro_lines, sections, heading, lines = meta.get("title", ""), [], [], None, []
    for line in body.splitlines():
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif line.startswith("# "):
            continue
        elif line.startswith("## "):
            if heading is not None:
                sections.append(Section(len(sections), heading, "\n".join(lines).strip()))
            heading, lines = line[3:].strip(), []
        elif heading is None:
            intro_lines.append(line)
        else:
            lines.append(line)
    if heading is not None:
        sections.append(Section(len(sections), heading, "\n".join(lines).strip()))
    if not title or not sections:
        raise ValueError(f"{slug}: needs a '# ' title and at least one '## ' section")
    intro = "\n".join(intro_lines).strip()
    if intro:  # The intro can hold facts too; it travels with the first section (anchor #muc-0).
        sections[0] = Section(0, sections[0].heading, f"{intro}\n\n{sections[0].content}".strip())
    updated = None
    if meta.get("updated_at"):
        updated = date.fromisoformat(meta["updated_at"])
    return Document(slug=slug, title=title, category=meta["category"], updated_at=updated,
                    intro="", sections=sections,
                    source_url=meta.get("url") or None)


def load_dir(directory: Path) -> list[Document]:
    paths = sorted(directory.glob("*.md"))
    clashing = [path.name for path in paths if path.stem.startswith("faq-")]
    if clashing:  # "faq-<id>" is the slug of pushed FAQ entries; a file must not overwrite one.
        raise ValueError(f"rename {', '.join(clashing)}: file names may not start with 'faq-'")
    return [parse(path.read_text(encoding="utf-8-sig"), path.stem) for path in paths]


def faq_document(faq: dict) -> Document:
    """A pushed FAQ entry becomes a one-section document, so search covers it like any page."""
    # The shop's keywords (synonyms such as "VAT") are indexed with the entry, not shown to the model.
    keywords = ", ".join(keyword for keyword in faq.get("keywords") or [] if keyword)
    return Document(slug=f"faq-{faq['id']}", title=faq["question"], category="faq", updated_at=None,
                    intro=f"Từ khoá: {keywords}" if keywords else "",
                    sections=[Section(0, faq["question"], faq["answer"])],
                    source_url=faq.get("source_url"), origin="faq")
