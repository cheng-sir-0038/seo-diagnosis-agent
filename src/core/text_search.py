"""Tokenization for PostgreSQL simple dictionaries, including CJK text."""

import re
import unicodedata

_WORDS = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]+")


def normalize_search_text(value: str) -> str:
    separated = re.sub(r"[①②③④⑤⑥⑦⑧⑨⑩]", " ", value)
    return unicodedata.normalize("NFKC", separated).casefold()


def search_tokens(value: str) -> set[str]:
    terms: set[str] = set()
    for word in _WORDS.findall(normalize_search_text(value)):
        if word.isascii() or len(word) <= 2:
            terms.add(word)
        else:
            for size in (2, 3):
                terms.update(word[i : i + size] for i in range(len(word) - size + 1))
    return {term for term in terms if len(term) > 1 or term.isdigit()}
