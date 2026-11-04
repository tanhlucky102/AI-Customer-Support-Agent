"""Index a shop's knowledge base.

    python -m rag.ingest --shop demo_shop               # data/kb/demo_shop/*.md
    python -m rag.ingest --shop demo_shop --contextual  # + LLM context sentences (Contextual Retrieval)

Contextual Retrieval is off by default: on this knowledge base, whose sections are written to be
self-contained, it did not improve Recall@5 (eval/results/ablation), and it costs one LLM call per
changed chunk.

Unchanged documents are skipped (content hash), removed files are deleted from the index, and
context sentences are cached per chunk, so re-running after an edit is cheap.
"""

import argparse
import asyncio
import time
from pathlib import Path

from rag import store
from rag.chunking import chunk_document, header
from rag.contextual import PROMPT_VERSION, contexts_for
from rag.documents import Document, faq_document, load_dir
from rag.models import embedder, embedder_name
from shared.settings import ROOT, env_flag, env_int


def settings_key(contextual: bool, max_tokens: int, overlap: int) -> list[str]:
    return [f"ctx={PROMPT_VERSION if contextual else 'off'}", f"T={max_tokens}", f"o={overlap}",
            f"emb={embedder_name()}"]


def _plan(shop_id: str, shop_url: str, document: Document, origin: str, settings: list[str],
          known: dict, force: bool, max_tokens: int, overlap: int) -> dict | None:
    """What to index for one document, or None when its fingerprint is unchanged."""
    # A pushed FAQ entry without its own page links to the shop's site, not to a /help page
    # that a level-0 shop does not have.
    source_url = document.source_url or (shop_url.rstrip("/") if origin == "faq"
                                         else f"{shop_url.rstrip('/')}/help/{document.slug}")
    fingerprint = document.fingerprint(*settings, source_url, str(document.updated_at), origin)
    if not force and known.get(document.slug) == fingerprint:
        return None
    sections = chunk_document(shop_id, document, max_tokens, overlap)
    children = {child.id: (section, child) for section, _, kids in sections for child in kids}
    return {"document": document, "source_url": source_url, "fingerprint": fingerprint,
            "sections": sections, "children": children}


def _write(shop_id: str, plan: dict, contexts: dict[str, str]) -> int:
    """Embed one planned document and replace it in the index; returns the number of chunks."""
    document, children = plan["document"], plan["children"]
    index_texts = {cid: "\n".join(part for part in (header(document, section), document.intro,
                                                    contexts.get(cid, ""), child.content) if part)
                   for cid, (section, child) in children.items()}
    ids = list(index_texts)
    vectors = dict(zip(ids, embedder().encode([index_texts[cid] for cid in ids])))
    store.replace_document(shop_id, document, plan["sections"], contexts, vectors,
                           fingerprint=plan["fingerprint"], source_url=plan["source_url"],
                           embedding_model=embedder_name(), index_texts=index_texts)
    return len(ids)


def _prune(shop_id: str, known: dict, documents: list[Document], origin: str, allow_mass_prune: bool) -> int:
    stale = sorted(set(known) - {document.slug for document in documents})
    # A wrong folder must not empty a shop: removing most of its pages needs explicit consent.
    # (A pushed FAQ is a full snapshot by contract, so it may shrink freely.)
    if origin == "file" and len(stale) > max(3, len(known) // 2) and not allow_mass_prune:
        raise ValueError(f"would remove {len(stale)} of {len(known)} indexed documents of {shop_id}; "
                         "check the folder, or pass --prune-all if that is intended")
    store.delete_documents(shop_id, stale, origin)
    return len(stale)


async def ingest_documents(shop_id: str, shop_url: str, documents: list[Document], *,
                           origin: str = "file", contextual: bool = False, max_tokens: int = 500,
                           overlap: int = 80, force: bool = False, prune: bool = True,
                           allow_mass_prune: bool = False) -> dict:
    """Index documents; async only because Contextual Retrieval calls the LLM."""
    started = time.perf_counter()
    known = store.fingerprints(shop_id, origin)
    settings = settings_key(contextual, max_tokens, overlap)
    stats = {"documents": len(documents), "indexed": 0, "skipped": 0, "removed": 0, "chunks": 0,
             "llm_calls": 0}
    for document in documents:
        plan = _plan(shop_id, shop_url, document, origin, settings, known, force, max_tokens, overlap)
        if plan is None:
            stats["skipped"] += 1
            continue
        contexts = {}
        if contextual:
            contexts, calls = await contexts_for(document.text, {cid: child.content for cid, (_, child)
                                                                 in plan["children"].items()})
            stats["llm_calls"] += calls
            if len(contexts) < len(plan["children"]):
                plan["fingerprint"] += "-incomplete"  # Some context calls failed: retry them next run.
        stats["chunks"] += _write(shop_id, plan, contexts)
        stats["indexed"] += 1
    if prune:
        stats["removed"] = _prune(shop_id, known, documents, origin, allow_mass_prune)
    stats["seconds"] = round(time.perf_counter() - started, 1)
    return stats


async def ingest_dir(shop_id: str, shop_url: str, directory: Path, **options) -> dict:
    if not directory.is_dir():
        raise FileNotFoundError(f"{directory} is not a folder; nothing was changed")
    documents = load_dir(directory)
    if not documents:
        raise ValueError(f"{directory} has no .md files; nothing was changed")
    return await ingest_documents(shop_id, shop_url, documents, **options)


def index_faqs_sync(shop_id: str, shop_url: str, faqs: list[dict]) -> dict:
    """Pushed FAQ entries are searchable like pages (no LLM context: each entry is self-contained).
    Synchronous, so the server runs it in a worker thread without a second event loop."""
    started = time.perf_counter()
    documents = [faq_document(faq) for faq in faqs]
    known = store.fingerprints(shop_id, "faq")
    settings = settings_key(False, 500, 80)
    stats = {"documents": len(documents), "indexed": 0, "skipped": 0, "removed": 0, "chunks": 0,
             "llm_calls": 0}
    for document in documents:
        plan = _plan(shop_id, shop_url, document, "faq", settings, known, False, 500, 80)
        if plan is None:
            stats["skipped"] += 1
            continue
        stats["chunks"] += _write(shop_id, plan, {})
        stats["indexed"] += 1
    stats["removed"] = _prune(shop_id, known, documents, "faq", True)
    stats["seconds"] = round(time.perf_counter() - started, 1)
    return stats


async def index_faqs(shop_id: str, shop_url: str, faqs: list[dict]) -> dict:
    """For scripts: the same as index_faqs_sync."""
    return index_faqs_sync(shop_id, shop_url, faqs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Index a shop's Markdown knowledge base")
    parser.add_argument("--shop", required=True)
    parser.add_argument("--dir", help="Default: data/kb/<shop>")
    parser.add_argument("--contextual", action="store_true", default=env_flag("RAG_CONTEXTUAL", False),
                        help="Prepend LLM-written context sentences to every chunk")
    parser.add_argument("--chunk-tokens", type=int, default=env_int("RAG_CHUNK_TOKENS", 500))
    parser.add_argument("--overlap", type=int, default=env_int("RAG_CHUNK_OVERLAP", 80))
    parser.add_argument("--force", action="store_true", help="Re-index unchanged documents too")
    parser.add_argument("--prune-all", action="store_true",
                        help="Allow removing more than half of the shop's indexed documents")
    args = parser.parse_args()

    from agent_server.store import init_db, shop_by_id
    init_db()
    shop = shop_by_id(args.shop)
    if not shop:
        parser.error(f"unknown shop {args.shop}; provision it first")
    directory = Path(args.dir) if args.dir else ROOT / "data" / "kb" / args.shop
    stats = asyncio.run(ingest_dir(args.shop, shop["shop_url"], directory, allow_mass_prune=args.prune_all,
                                   contextual=args.contextual, max_tokens=args.chunk_tokens,
                                   overlap=args.overlap, force=args.force))
    print(stats)


if __name__ == "__main__":
    main()
