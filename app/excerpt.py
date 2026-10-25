from __future__ import annotations

import re

from app.content import sanitize_untrusted_text

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。！？])\s+|\n{2,}")
_WORD_BREAK = re.compile(r"\s")


def make_snippet(text: str, query: str, *, minimum: int = 100, maximum: int = 200) -> str:
    return _excerpt(text, query, minimum=minimum, maximum=maximum)


def make_summary(text: str, query: str, *, minimum: int = 600, maximum: int = 800) -> str:
    return _excerpt(text, query, minimum=minimum, maximum=maximum)


def _excerpt(text: str, query: str, *, minimum: int, maximum: int) -> str:
    cleaned = sanitize_untrusted_text(text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    if len(cleaned) <= maximum:
        return cleaned

    sentences = [part.strip() for part in _SENTENCE_BOUNDARY.split(cleaned) if part.strip()]
    if not sentences:
        sentences = [cleaned]

    terms = [term for term in re.findall(r"\w+", query.casefold(), flags=re.UNICODE) if term]
    scored: list[tuple[int, float, int, str]] = []
    for index, sentence in enumerate(sentences):
        lowered = sentence.casefold()
        hits = sum(1 for term in set(terms) if term in lowered)
        score = hits * 2.0 - index * 0.015
        scored.append((index, score, hits, sentence))

    selected: list[str] = []
    used = 0
    first = sentences[0]
    selected.append(first)
    used = len(first)

    for index, score, hits, sentence in sorted(scored, key=lambda item: (-item[1], item[0])):
        if index == 0:
            continue
        if hits == 0 and len(selected) > 2:
            continue
        if used + 1 + len(sentence) > maximum:
            continue
        selected.append(sentence)
        used += 1 + len(sentence)
        if used >= minimum:
            break

    excerpt = " ".join(selected)
    if len(excerpt) > maximum:
        excerpt = _truncate(excerpt, maximum)
    return excerpt.strip()


def _truncate(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    cut = value[: maximum + 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-") + "…"
