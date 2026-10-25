from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from bs4 import BeautifulSoup, Comment
from markdownify import markdownify as convert_to_markdown


_INJECTION_PATTERNS = (
    re.compile(r"\bignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+instructions?\b", re.I),
    re.compile(r"\bdisregard\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?\b", re.I),
    re.compile(r"\b(?:reveal|show|print)\s+(?:your\s+)?system\s+prompt\b", re.I),
    re.compile(r"\byou\s+are\s+now\s+in\s+developer\s+mode\b", re.I),
    re.compile(r"\bnew\s+system\s+instructions?\s*:", re.I),
)
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class ExtractedPage:
    title: str
    content: str


def extract_page(raw_html: str) -> ExtractedPage:
    soup = BeautifulSoup(raw_html, "html.parser")
    title = _extract_title(soup)
    _remove_unsafe_nodes(soup)
    root = soup.find("main") or soup.find("article") or soup.body or soup
    markdown = convert_to_markdown(str(root), heading_style="ATX", bullets="-")
    markdown = _normalize_markdown(markdown)
    markdown = sanitize_untrusted_text(markdown)
    return ExtractedPage(title=title, content=markdown)


def html_to_text(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    _remove_unsafe_nodes(soup)
    return sanitize_untrusted_text(soup.get_text(" ", strip=True))


def sanitize_untrusted_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _ZERO_WIDTH.sub("", value)
    value = _CONTROL.sub("", value)

    cleaned_lines: list[str] = []
    for line in value.split("\n"):
        if any(pattern.search(line) for pattern in _INJECTION_PATTERNS):
            cleaned_lines.append("> [Prompt-injection instruction removed]")
        else:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def _extract_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.string:
        title = soup.title.get_text(" ", strip=True)
    else:
        heading = soup.find("h1")
        title = heading.get_text(" ", strip=True) if heading else ""
    return sanitize_untrusted_text(title)[:500]


def _remove_unsafe_nodes(soup: BeautifulSoup) -> None:
    for comment in soup.find_all(string=lambda item: isinstance(item, Comment)):
        comment.extract()

    for tag in soup.find_all(
        [
            "script",
            "style",
            "noscript",
            "template",
            "svg",
            "canvas",
            "math",
            "iframe",
            "object",
            "embed",
            "form",
            "button",
            "input",
            "nav",
            "footer",
            "header",
            "aside",
        ]
    ):
        tag.decompose()

    for element in soup.find_all(True):
        attrs = element.attrs or {}
        style = str(attrs.get("style", "")).casefold().replace(" ", "")
        hidden = (
            "hidden" in attrs
            or str(attrs.get("aria-hidden", "")).casefold() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )
        if hidden:
            element.decompose()
            continue
        for attribute in ("href", "src", "action"):
            value = str(attrs.get(attribute, "")).strip().casefold()
            if value.startswith(("javascript:", "vbscript:", "data:")):
                del attrs[attribute]


def _normalize_markdown(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()
