"""Small text helpers: split identifiers/labels into comparable lowercase words."""
from __future__ import annotations

import re

_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
MIN_STEM = 4  # shared prefix length for two words to count as the same stem


def words(text: str | None) -> list[str]:
    """'SoldToParty' / 'Sold-to Party' / 'A_SalesOrder' -> lowercase words (singular)."""
    if not text:
        return []
    out = []
    for chunk in re.split(r"[^0-9A-Za-z]+", text):
        out.extend(_singular(w.lower()) for w in _CAMEL.findall(chunk))
    return out


def _singular(word: str) -> str:
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def squash(text: str | None) -> str:
    """Words joined without separators, for exact-equality comparison."""
    return "".join(words(text))


def same_stem(a: str, b: str) -> bool:
    """True if two words are equal or share a long common prefix (party ~ partner)."""
    if a == b:
        return True
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n >= MIN_STEM and n >= 0.8 * min(len(a), len(b))
