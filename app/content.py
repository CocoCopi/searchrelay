from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO

import trafilatura
from bs4 import BeautifulSoup, Comment
from markdownify import markdownify as convert_to_markdown

try:  # Optional but recommended: used when the response has no usable charset.
    from charset_normalizer import from_bytes as _detect_charset
except ImportError:  # pragma: no cover - exercised only without the extra
    _detect_charset = None

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - exercised only without the extra
    PdfReader = None


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
    """Extract the main article as Markdown, excluding page chrome.

    Trafilatura performs boilerplate removal before the result is used for a
    snippet or summary. The conservative fallback only accepts explicit
    ``main``/``article`` roots so navigation, cookie notices, and link lists
    cannot leak into extracted content. Tables and fenced code blocks are
    preserved; code fences inherit a language hint when the source HTML
    declares one.
    """

    soup = BeautifulSoup(raw_html, "html.parser")
    title = _extract_title(soup)
    _remove_unsafe_nodes(soup)
    _remove_page_chrome(soup)
    code_languages = _code_languages(soup)
    markdown = trafilatura.extract(
        str(soup),
        output_format="markdown",
        include_links=False,
        include_tables=True,
        include_comments=False,
        favor_recall=True,
    )

    if not markdown or not markdown.strip():
        root = soup.find("main") or soup.find("article")
        markdown = (
            convert_to_markdown(str(root), heading_style="ATX", bullets="-")
            if root is not None
            else ""
        )

    markdown = _apply_code_languages(markdown, code_languages)
    markdown = _normalize_markdown(markdown)
    markdown = sanitize_untrusted_text(markdown)
    return ExtractedPage(title=title, content=markdown)


def decode_bytes(body: bytes, declared_charset: str | None = None) -> str:
    """Decode a response body, detecting the charset when it is not declared.

    The declared charset wins when it can decode the body. Otherwise the
    charset is detected from the bytes themselves, and UTF-8 with replacement
    characters is the final fallback.
    """

    if declared_charset:
        try:
            return body.decode(declared_charset)
        except (LookupError, UnicodeDecodeError):
            pass
    if _detect_charset is not None and body:
        try:
            match = _detect_charset(body).best()
        except Exception:  # pragma: no cover - defensive, detector is best effort
            match = None
        if match is not None:
            try:
                return str(match)
            except (UnicodeDecodeError, LookupError):  # pragma: no cover - defensive
                pass
    return body.decode("utf-8", errors="replace")


def pdf_to_markdown(data: bytes) -> ExtractedPage:
    """Convert a PDF document to Markdown.

    PDF text is extracted page by page and joined with a horizontal rule so
    page boundaries stay visible. PDF is not a rich document format, so the
    result is plain text rather than reconstructed headings and tables.
    """

    if PdfReader is None:  # pragma: no cover - exercised only without the extra
        raise ValueError("PDF support requires the 'pypdf' package")
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:
        raise ValueError("The PDF could not be parsed") from exc
    pages: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:  # pragma: no cover - malformed PDFs must not crash
            text = ""
        text = text.strip()
        if text:
            pages.append(text)
    if not pages:
        raise ValueError("The PDF contains no extractable text")
    title = ""
    metadata = getattr(reader, "metadata", None)
    if metadata is not None:
        title = str(metadata.get("/Title") or "").strip()
    content = "\n\n---\n\n".join(pages)
    return ExtractedPage(title=sanitize_untrusted_text(title)[:500], content=content)


def html_to_text(raw_html: str) -> str:
    """Extract main text from an HTML fragment without page boilerplate."""

    soup = BeautifulSoup(raw_html, "html.parser")
    _remove_unsafe_nodes(soup)
    _remove_page_chrome(soup)
    extracted = trafilatura.extract(
        str(soup),
        output_format="txt",
        include_links=False,
        include_tables=True,
        include_comments=False,
        favor_recall=True,
    )
    if extracted and extracted.strip():
        return sanitize_untrusted_text(extracted)

    root = soup.find("main") or soup.find("article")
    if root is None:
        return ""
    return sanitize_untrusted_text(root.get_text(" ", strip=True))


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


def _remove_page_chrome(soup: BeautifulSoup) -> None:
    """Remove common consent notices and navigation-style link collections."""

    chrome_markers = (
        "cookie",
        "consent",
        "gdpr",
        "privacy-notice",
        "newsletter",
        "link-list",
        "related-links",
        "social-links",
        "site-navigation",
    )
    for element in list(soup.find_all(True)):
        attrs = element.attrs or {}
        role = str(attrs.get("role", "")).casefold()
        markers = " ".join(
            [
                str(attrs.get("id", "")),
                " ".join(attrs.get("class", []))
                if isinstance(attrs.get("class", []), list)
                else str(attrs.get("class", "")),
            ]
        ).casefold()
        if role in {"navigation", "menu"} or any(
            marker in markers for marker in chrome_markers
        ):
            element.decompose()

    _remove_link_lists(soup)
    _remove_cookie_leaf_nodes(soup)


def _remove_link_lists(soup: BeautifulSoup) -> None:
    for list_element in list(soup.find_all(["ul", "ol"])):
        items = list_element.find_all("li", recursive=False)
        if len(items) < 2:
            continue
        linked_items = sum(1 for item in items if item.find("a") is not None)
        if linked_items * 2 >= len(items):
            list_element.decompose()


def _remove_cookie_leaf_nodes(soup: BeautifulSoup) -> None:
    notices = ("accept all cookies", "accept cookies", "we use cookies", "cookie preferences")
    for element in list(soup.find_all(["div", "p", "span"])):
        if element.find(True) is not None:
            continue
        text = element.get_text(" ", strip=True).casefold()
        if len(text) <= 300 and any(notice in text for notice in notices):
            element.decompose()


def _normalize_markdown(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


_LANGUAGE_CLASS_RE = re.compile(r"(?:language|lang|highlight)-([A-Za-z0-9_+#.-]+)")


def _code_languages(soup: BeautifulSoup) -> list[str]:
    """Collect code-block languages in document order."""

    languages: list[str] = []
    for block in soup.find_all("pre"):
        code = block.find("code")
        candidates = [block, code] if code is not None else [block]
        language = ""
        for candidate in candidates:
            classes = candidate.get("class") if candidate is not None else None
            if not classes:
                continue
            if isinstance(classes, str):
                classes = [classes]
            for value in classes:
                match = _LANGUAGE_CLASS_RE.search(str(value))
                if match:
                    language = match.group(1).lower()
                    break
            if language:
                break
        languages.append(language)
    return languages


def _apply_code_languages(markdown: str, languages: list[str]) -> str:
    """Add language hints to fenced code blocks that lack them."""

    if not markdown or not languages:
        return markdown
    hints = list(languages)
    index = 0
    in_code_block = False
    output: list[str] = []
    for line in markdown.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_code_block:
                in_code_block = True
                language = hints[index] if index < len(hints) else ""
                index += 1
                if language and stripped == "```":
                    line = f"```{language}"
            else:
                in_code_block = False
        output.append(line)
    return "\n".join(output)
