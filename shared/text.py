"""Vietnamese text helpers used for keyword search and rule matching."""

import re
import unicodedata

WORD = re.compile(r"[a-z0-9]+")

# Function words that carry no topic. Used only to build the Postgres keyword query, which has no
# IDF weighting; BM25 down-weights common words by itself.
# Folded syllables that are also content words ("mã", "còn", "lãi", "thẻ", "đăng", "cài") are kept.
STOPWORDS = frozenset("""
a ah ak ban biet cac cho chua co cua cung duoc em gi gium giup ha hay khong kia la luon minh mot
muon nao ne neu nha nhe nhi nhu nhung oi roi sao se shop thi toi trong va vay vi voi xin ok
is an to of for and or in on my me i you do does what how
""".split())


def fold(text: str) -> str:
    """Lowercase and strip Vietnamese diacritics: 'Đổi trả' -> 'doi tra'."""
    text = text.lower().replace("đ", "d")
    return "".join(char for char in unicodedata.normalize("NFD", text)
                   if unicodedata.category(char) != "Mn")


def words(text: str) -> list[str]:
    return WORD.findall(fold(text))


def terms(text: str) -> list[str]:
    """Syllables plus adjacent-syllable bigrams ('doi_tra'), since Vietnamese words span syllables."""
    tokens = words(text)
    return tokens + [f"{left}_{right}" for left, right in zip(tokens, tokens[1:])]


def query_terms(text: str) -> list[str]:
    tokens = [token for token in words(text) if token not in STOPWORDS]
    seen, result = set(), []
    for term in tokens + [f"{left}_{right}" for left, right in zip(tokens, tokens[1:])]:
        if term not in seen:
            seen.add(term)
            result.append(term)
    return result
