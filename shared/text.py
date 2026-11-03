"""Vietnamese text helpers used for keyword search and rule matching."""

import re
import unicodedata

WORD = re.compile(r"[a-z0-9]+")

# Function words that carry no topic, dropped from search queries. A word typed with diacritics
# is compared as typed, so content words that fold to the same letters ("muộn" vs "muốn", "cửa" vs
# "của", "ví" vs "vì") survive; a word typed without diacritics is compared in folded form.
STOPWORDS = frozenset("""
à ạ ah ak bạn biết các cho chưa có của cũng được em gì giùm giúp hả hay không ko kia là luôn mình
một muốn nào nè nếu nha nhé nhỉ như những ơi rồi sẽ shop thì tôi trong và vậy vì với xin ok
is an to of for and or in on my me i you do does what how
""".split())
FOLDED_STOPWORDS = frozenset("""
a ah ak ban biet cac cho chua co cua cung duoc em gi gium giup ha hay khong ko kia la luon minh mot
muon nao ne neu nha nhe nhi nhu nhung oi roi se shop thi toi trong va vay vi voi xin ok
is an to of for and or in on my me i you do does what how
""".split())
MAX_TERM = 60  # Longer "words" are noise (pasted codes, URLs) and can exceed index limits.


def fold(text: str) -> str:
    """Lowercase and strip Vietnamese diacritics: 'Đổi trả' -> 'doi tra'."""
    text = text.lower().replace("đ", "d")
    return "".join(char for char in unicodedata.normalize("NFD", text)
                   if unicodedata.category(char) != "Mn")


def words(text: str) -> list[str]:
    return [word for word in WORD.findall(fold(text)) if len(word) <= MAX_TERM]


def _stopword(raw: str, folded: str) -> bool:
    return raw in STOPWORDS if raw != folded else folded in FOLDED_STOPWORDS


def terms(text: str) -> list[str]:
    """Syllables plus adjacent-syllable bigrams ('doi_tra'), since Vietnamese words span syllables."""
    tokens = words(text)
    return tokens + [f"{left}_{right}" for left, right in zip(tokens, tokens[1:])]


def query_terms(text: str) -> list[str]:
    """Content syllables plus bigrams of adjacent syllables (in the original order, so a removed
    function word never glues two unrelated syllables together)."""
    raw = [word for word in re.findall(r"\w+", text.lower().replace("_", " ")) if len(word) <= MAX_TERM]
    pairs = [(word, fold(word)) for word in raw]
    pairs = [(word, folded) for word, folded in pairs if WORD.fullmatch(folded)]
    keep = [not _stopword(word, folded) for word, folded in pairs]
    tokens = [folded for (_, folded), kept in zip(pairs, keep) if kept]
    bigrams = [f"{pairs[i][1]}_{pairs[i + 1][1]}" for i in range(len(pairs) - 1) if keep[i] or keep[i + 1]]
    seen, result = set(), []
    for term in tokens + bigrams:
        if term not in seen:
            seen.add(term)
            result.append(term)
    return result


# English words that are not also common Vietnamese syllables typed without diacritics
# ("can" = cần, "the" = thẻ, "do" = do/đỏ, "to" = to, "in" = in, "an" = ăn are left out on purpose).
ENGLISH_WORDS = frozenset("""
is are am was were be been i you your my me we our it its this that these those what how why where
when which who whose there here have has had will would should could does did not and or with for of
from about please get use need want any some many much order orders policy return shipping if than
then they them he she his her won tell show give make know like just still while can't don't
hello hi hey thanks thank lot stock available discount discounts today tomorrow deliver delivery
delivered price prices cost warranty refund cancel change address phone phones buy pay payment
store hours status track tracking receive received arrive arrived late sell sold open close
""".split())
UNACCENTED_VIETNAMESE = frozenset("""
khong ko k duoc dc thi la co toi minh ban sao nao gi nhe oi cua cho voi mua bao nhieu hang don roi
em anh chi giup gium nha vay het con cai nay dau giao doi tra
""".split())


def source_label(hit: dict) -> str:
    """Citation label 'document – section'; an FAQ entry's section repeats its title, so it is shown once."""
    title, section = hit["doc_title"], hit.get("section") or ""
    return title if not section or section == title else f"{title} – {section}"


def looks_english(text: str) -> bool:
    """True for a message written in English, so the answer can be steered to English even though
    the documents and tool results are Vietnamese. Unaccented Vietnamese is not English."""
    if fold(text) != text.lower().replace("đ", "d"):
        return False  # It has Vietnamese diacritics.
    tokens = re.findall(r"\b[a-z']+\b", text.lower())  # "250k" is not the word "k".
    if any(token in UNACCENTED_VIETNAMESE for token in tokens):
        return False
    return sum(token in ENGLISH_WORDS for token in tokens) >= 2
