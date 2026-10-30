"""RAG pipeline: Markdown documents -> sections/chunks -> embeddings + keyword index -> hybrid search.

Contract C1: rag.search.search(query, shop_id, category=None, k=5) -> list of passages with
chunk_id, content, doc_title, section, url and score.
"""
