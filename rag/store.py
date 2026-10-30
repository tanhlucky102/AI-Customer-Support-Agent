"""Knowledge-base tables in PostgreSQL + pgvector."""

from collections import defaultdict

import numpy as np

from shared.db import connect
from shared.text import terms


def tsvector_literal(text: str) -> str:
    """Positions for syllables and syllable bigrams of the accent-folded text.

    Postgres has no Vietnamese dictionary; folding in Python plays the role of `unaccent`, and the
    bigram lexemes ('doi_tra') let multi-syllable words match as units.
    """
    positions: dict[str, list[int]] = defaultdict(list)
    tokens = terms(text)
    unigrams = len([token for token in tokens if "_" not in token])
    for index, token in enumerate(tokens):
        position = index + 1 if index < unigrams else index - unigrams + 1
        if len(positions[token]) < 256:
            positions[token].append(min(position, 16383))
    return " ".join(f"{term}:{','.join(map(str, spots))}" for term, spots in positions.items())


def fingerprints(shop_id: str, origin: str) -> dict[str, str]:
    with connect() as db:
        rows = db.execute("SELECT slug, content_hash FROM kb_documents WHERE shop_id = %s "
                          "AND origin = %s", (shop_id, origin)).fetchall()
    return {row["slug"]: row["content_hash"] for row in rows}


def delete_documents(shop_id: str, slugs: list[str]) -> None:
    if slugs:
        with connect() as db:
            db.execute("DELETE FROM kb_documents WHERE shop_id = %s AND slug = ANY(%s)",
                       (shop_id, slugs))


def replace_document(shop_id: str, document, sections: list, contexts: dict[str, str],
                     vectors: dict[str, np.ndarray], *, fingerprint: str, source_url: str | None,
                     embedding_model: str, index_texts: dict[str, str]) -> None:
    """Replace one document with its sections and embedded chunks in a single transaction."""
    with connect() as db:
        db.execute("DELETE FROM kb_documents WHERE shop_id = %s AND slug = %s",
                   (shop_id, document.slug))
        doc_id = db.execute("""INSERT INTO kb_documents
            (shop_id, slug, title, category, source_url, origin, updated_at, content_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""", (
            shop_id, document.slug, document.title, document.category, source_url,
            document.origin, document.updated_at, fingerprint)).fetchone()["id"]
        with db.cursor() as cursor:
            for section, section_id, children in sections:
                cursor.execute("""INSERT INTO kb_sections
                    (id, doc_id, shop_id, position, heading, content, token_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)""", (
                    section_id, doc_id, shop_id, section.position, section.heading,
                    section.content, sum(child.token_count for child in children)))
                cursor.executemany("""INSERT INTO kb_chunks
                    (id, doc_id, section_id, shop_id, position, content, context_prefix,
                     token_count, embedding, content_tsv, embedding_model)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::tsvector, %s)""", [
                    (child.id, doc_id, section_id, shop_id, child.position, child.content,
                     contexts.get(child.id, ""), child.token_count, vectors[child.id],
                     tsvector_literal(index_texts[child.id]), embedding_model)
                    for child in children])


def signature(shop_id: str) -> str:
    """Changes whenever the shop's indexed documents change (used to refresh in-memory BM25)."""
    with connect() as db:
        row = db.execute("""SELECT count(*) AS n, coalesce(md5(string_agg(content_hash, ','
                            ORDER BY slug)), '') AS h FROM kb_documents WHERE shop_id = %s""",
                         (shop_id,)).fetchone()
    return f"{row['n']}:{row['h']}"


def chunk_texts(shop_id: str) -> list[dict]:
    with connect() as db:
        return db.execute("""SELECT c.id, c.section_id, d.category,
                d.title || ' › ' || s.heading || E'\\n' || c.context_prefix || E'\\n' || c.content AS text
            FROM kb_chunks c JOIN kb_sections s ON s.id = c.section_id
            JOIN kb_documents d ON d.id = c.doc_id WHERE c.shop_id = %s""", (shop_id,)).fetchall()


def vector_candidates(shop_id: str, vector: np.ndarray, limit: int,
                      category: str | None) -> list[dict]:
    with connect() as db:
        db.execute("SET LOCAL hnsw.ef_search = 100")
        return db.execute("""SELECT c.id, c.section_id, 1 - (c.embedding <=> %s) AS similarity
            FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id
            WHERE c.shop_id = %s AND (%s::text IS NULL OR d.category = %s)
            ORDER BY c.embedding <=> %s LIMIT %s""",
                          (vector, shop_id, category, category, vector, limit)).fetchall()


def tsrank_candidates(shop_id: str, query: list[str], limit: int, category: str | None) -> list[dict]:
    if not query:
        return []
    with connect() as db:
        # The query is already folded into the same lexemes as the index, so it is cast directly
        # instead of going through to_tsquery (which would split the 'doi_tra' bigrams).
        return db.execute("""SELECT c.id, c.section_id, ts_rank_cd(c.content_tsv, query.q) AS score
            FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id
            CROSS JOIN (SELECT %s::tsquery AS q) AS query
            WHERE c.shop_id = %s AND c.content_tsv @@ query.q
              AND (%s::text IS NULL OR d.category = %s)
            ORDER BY score DESC LIMIT %s""",
                          (" | ".join(query), shop_id, category, category, limit)).fetchall()


def sections(section_ids: list[str]) -> dict[str, dict]:
    if not section_ids:
        return {}
    with connect() as db:
        rows = db.execute("""SELECT s.id, s.heading, s.content, d.title, d.slug, d.category,
                d.source_url FROM kb_sections s JOIN kb_documents d ON d.id = s.doc_id
            WHERE s.id = ANY(%s)""", (section_ids,)).fetchall()
    return {row["id"]: row for row in rows}


def documents_text(shop_id: str) -> list[dict]:
    """All of a shop's documents in full (the 'whole KB in context' baseline)."""
    with connect() as db:
        rows = db.execute("""SELECT d.slug, d.title, s.heading, s.content FROM kb_sections s
            JOIN kb_documents d ON d.id = s.doc_id WHERE d.shop_id = %s
            ORDER BY d.slug, s.position""", (shop_id,)).fetchall()
    return rows
