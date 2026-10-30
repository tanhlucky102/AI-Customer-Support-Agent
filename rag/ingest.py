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
from rag.models import embedder
from shared.settings import ROOT, env_flag, env_int


def settings_key(contextual: bool, max_tokens: int, overlap: int) -> list[str]:
    return [f"ctx={PROMPT_VERSION if contextual else 'off'}", f"T={max_tokens}", f"o={overlap}",
            f"emb={embedder().name}"]


async def ingest_documents(shop_id: str, shop_url: str, documents: list[Document], *,
                           origin: str = "file", contextual: bool = False, max_tokens: int = 500,
                           overlap: int = 80, force: bool = False, prune: bool = True) -> dict:
    started = time.perf_counter()
    known = store.fingerprints(shop_id, origin)
    settings = settings_key(contextual, max_tokens, overlap)
    stats = {"documents": len(documents), "indexed": 0, "skipped": 0, "removed": 0, "chunks": 0,
             "llm_calls": 0}
    for document in documents:
        fingerprint = document.fingerprint(*settings)
        if not force and known.get(document.slug) == fingerprint:
            stats["skipped"] += 1
            continue
        sections = chunk_document(shop_id, document, max_tokens, overlap)
        children = {child.id: (section, child) for section, _, kids in sections for child in kids}
        contexts = {}
        if contextual:
            contexts, calls = await contexts_for(document.text, {cid: child.content for cid, (_, child)
                                                                 in children.items()})
            stats["llm_calls"] += calls
        index_texts = {cid: "\n".join(part for part in (header(document, section), contexts.get(cid, ""),
                                                        child.content) if part)
                       for cid, (section, child) in children.items()}
        ids = list(index_texts)
        vectors = dict(zip(ids, embedder().encode([index_texts[cid] for cid in ids])))
        source_url = document.source_url or f"{shop_url.rstrip('/')}/help/{document.slug}"
        store.replace_document(shop_id, document, sections, contexts, vectors,
                               fingerprint=fingerprint, source_url=source_url,
                               embedding_model=embedder().name, index_texts=index_texts)
        stats["indexed"] += 1
        stats["chunks"] += len(ids)
    if prune:
        stale = sorted(set(known) - {document.slug for document in documents})
        store.delete_documents(shop_id, stale)
        stats["removed"] = len(stale)
    stats["seconds"] = round(time.perf_counter() - started, 1)
    return stats


async def ingest_dir(shop_id: str, shop_url: str, directory: Path, **options) -> dict:
    return await ingest_documents(shop_id, shop_url, load_dir(directory), **options)


async def index_faqs(shop_id: str, shop_url: str, faqs: list[dict]) -> dict:
    """Pushed FAQ entries are searchable like pages (no LLM context: each entry is self-contained)."""
    return await ingest_documents(shop_id, shop_url, [faq_document(faq) for faq in faqs],
                                  origin="faq", contextual=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Index a shop's Markdown knowledge base")
    parser.add_argument("--shop", required=True)
    parser.add_argument("--dir", help="Default: data/kb/<shop>")
    parser.add_argument("--contextual", action="store_true", default=env_flag("RAG_CONTEXTUAL", False),
                        help="Prepend LLM-written context sentences to every chunk")
    parser.add_argument("--chunk-tokens", type=int, default=env_int("RAG_CHUNK_TOKENS", 500))
    parser.add_argument("--overlap", type=int, default=env_int("RAG_CHUNK_OVERLAP", 80))
    parser.add_argument("--force", action="store_true", help="Re-index unchanged documents too")
    args = parser.parse_args()

    from agent_server.store import init_db, shop_by_id
    init_db()
    shop = shop_by_id(args.shop)
    if not shop:
        parser.error(f"unknown shop {args.shop}; provision it first")
    directory = Path(args.dir) if args.dir else ROOT / "data" / "kb" / args.shop
    stats = asyncio.run(ingest_dir(args.shop, shop["shop_url"], directory,
                                   contextual=args.contextual, max_tokens=args.chunk_tokens,
                                   overlap=args.overlap, force=args.force))
    print(stats)


if __name__ == "__main__":
    main()
