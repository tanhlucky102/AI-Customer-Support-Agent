"""RAG pipeline: document format, chunking, keyword ranking, fusion, PII masking."""

import unittest

from tests import support

from rag.bm25 import BM25Index
from rag.chunking import count_tokens, split_section
from rag.documents import CATEGORIES, Section, load_dir, parse
from rag.search import rrf, search
from rag.store import tsvector_literal
from shared.pii import mask_address, mask_email, mask_phone, mask_text
from shared.text import fold, query_terms, terms


class DocumentsTest(unittest.TestCase):
    def test_every_knowledge_base_file_parses_with_unique_headings(self):
        for shop in ("demo_shop", "second_shop"):
            documents = load_dir(support.ROOT / "data" / "kb" / shop)
            self.assertGreaterEqual(len(documents), 8)
            for document in documents:
                headings = [section.heading for section in document.sections]
                self.assertEqual(len(headings), len(set(headings)), document.slug)
                self.assertIn(document.category, CATEGORIES)

    def test_missing_front_matter_is_rejected(self):
        with self.assertRaises(ValueError):
            parse("# Title\n\n## A\nx", "bad")


class ChunkingTest(unittest.TestCase):
    def test_sections_split_at_sentence_boundaries_with_overlap(self):
        text = " ".join(f"Câu số {n} nói về chính sách đổi trả của cửa hàng." for n in range(40))
        chunks = split_section(Section(0, "H", text), max_tokens=60, overlap=15)
        self.assertGreater(len(chunks), 3)
        for chunk in chunks:
            self.assertLessEqual(count_tokens(chunk), 60)
        first_last = chunks[0].split("\n")[-1]
        self.assertIn(first_last, chunks[1])  # Overlap carries the last sentence forward.

    def test_short_section_is_one_chunk(self):
        self.assertEqual(len(split_section(Section(0, "H", "Ngắn gọn."), 500, 80)), 1)


class RankingTest(unittest.TestCase):
    def test_fold_and_bigrams(self):
        self.assertEqual(fold("Đổi TRẢ điện thoại"), "doi tra dien thoai")
        self.assertIn("doi_tra", terms("đổi trả"))
        self.assertNotIn("khong", query_terms("có được đổi trả không"))

    def test_bm25_prefers_rare_terms(self):
        rows = [{"id": "a", "section_id": "A", "category": "x", "text": "điện thoại kích hoạt eSIM"},
                {"id": "b", "section_id": "B", "category": "x", "text": "điện thoại bảo hành"},
                {"id": "c", "section_id": "C", "category": "x", "text": "điện thoại đổi trả"}]
        hits = BM25Index(rows).search(query_terms("điện thoại esim"), 3)
        self.assertEqual(hits[0]["id"], "a")

    def test_rrf_uses_ranks_only(self):
        scores = rrf([["a", "b"], ["b", "c"]], k=60)
        self.assertEqual(max(scores, key=scores.get), "b")
        self.assertAlmostEqual(scores["b"], 1 / 62 + 1 / 61)

    def test_tsvector_literal_is_safe(self):
        literal = tsvector_literal("Đổi trả: 30 ngày!")
        self.assertIn("doi_tra:1", literal)
        self.assertNotIn("'", literal)


class SearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.reset_database()
        from agent_server.store import DEMO_SHOP_URL
        support.ingest_kb("demo_shop", DEMO_SHOP_URL)

    def test_hybrid_search_returns_parent_sections_with_citation_fields(self):
        hits = search("kích hoạt eSIM trên điện thoại", "demo_shop")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["doc"], "kich-hoat-esim")
        for key in ("chunk_id", "content", "doc_title", "section", "url", "score"):
            self.assertIn(key, hits[0])
        self.assertEqual(len({hit["section_id"] for hit in hits}), len(hits))

    def test_every_keyword_branch_runs(self):
        from rag.search import SearchConfig
        for keyword in ("bm25", "tsrank"):
            hits = search("kích hoạt eSIM", "demo_shop", config=SearchConfig(
                vector=False, keyword=keyword, rerank=False), apply_threshold=False)
            self.assertEqual(hits[0]["doc"], "kich-hoat-esim", keyword)

    def test_reingest_skips_unchanged_documents(self):
        from agent_server.store import DEMO_SHOP_URL
        stats = support.ingest_kb("demo_shop", DEMO_SHOP_URL)
        self.assertEqual(stats["indexed"], 0)
        self.assertEqual(stats["skipped"], stats["documents"])


class PiiTest(unittest.TestCase):
    def test_masking(self):
        self.assertEqual(mask_phone("0901234567"), "090****567")
        self.assertEqual(mask_email("an@demo.vn"), "a***@demo.vn")
        self.assertEqual(mask_address("12 Lê Lợi, Quận 1, TP. Hồ Chí Minh"), "***, Quận 1, TP. Hồ Chí Minh")
        self.assertEqual(mask_text("gọi 0912 345 678 hoặc an@demo.vn"), "gọi 091****678 hoặc a***@demo.vn")


if __name__ == "__main__":
    unittest.main()
