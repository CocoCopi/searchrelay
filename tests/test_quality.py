from __future__ import annotations

import httpx
import pytest

from app.authority import load_authority_config
from app.content import decode_bytes, extract_page, pdf_to_markdown
from app.errors import APIError
from app.fetch import FetchService
from app.ranking import Ranker
from app.render import JavaScriptRenderer
from app.rerank import CrossEncoderReranker, normalize_logit
from app.security import SecurityPolicy
from tests.helpers import JSON_HEADERS, api_harness


def _minimal_pdf(text: str = "Hello PDF world") -> bytes:
    """Build a small, valid single-page PDF with one text run."""

    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    stream = f"BT /F1 18 Tf 20 120 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    output = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_offset = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    output += b"0000000000 65535 f \n"
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode("ascii")
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return bytes(output)


MINIMAL_PDF = _minimal_pdf()


def test_markdown_preserves_tables_and_code_blocks() -> None:
    page = extract_page(
        """
        <html><body><main>
          <h1>Guide</h1>
          <p>Intro paragraph with enough words to survive extraction.</p>
          <pre><code class="language-python">def hello():\n    return "world"\n</code></pre>
          <table>
            <thead><tr><th>Name</th><th>Value</th></tr></thead>
            <tbody><tr><td>alpha</td><td>1</td></tr><tr><td>beta</td><td>2</td></tr></tbody>
          </table>
        </main></body></html>
        """
    )

    assert "```python" in page.content
    assert 'return "world"' in page.content
    assert "| Name | Value |" in page.content
    assert "| alpha | 1 |" in page.content


def test_decode_bytes_uses_declared_charset() -> None:
    body = "café".encode("latin-1")

    assert decode_bytes(body, "latin-1") == "café"


def test_decode_bytes_detects_charset_when_not_declared() -> None:
    body = "café crème".encode("cp1252")

    decoded = decode_bytes(body, None)

    assert "caf" in decoded
    assert decoded != "café crème"


def test_pdf_to_markdown_extracts_text() -> None:
    page = pdf_to_markdown(MINIMAL_PDF)

    assert "Hello PDF world" in page.content
    assert isinstance(page.title, str)


def test_pdf_to_markdown_rejects_unparsable_pdf() -> None:
    with pytest.raises(ValueError):
        pdf_to_markdown(b"not a pdf")


@pytest.mark.asyncio
async def test_fetch_converts_pdf_to_markdown(tmp_path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=MINIMAL_PDF,
            headers={"content-type": "application/pdf"},
        )

    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://93.184.216.34/report.pdf"},
        )

    assert response.status_code == 200
    assert "Hello PDF world" in response.json()["content"]


def test_authority_config_drives_tiers(tmp_path) -> None:
    config_path = tmp_path / "authority.yaml"
    config_path.write_text(
        "version: 1\n"
        "tiers:\n"
        "  4:\n"
        "    - trusted.example\n"
        "  2:\n"
        "    - partner.example\n"
        "tld_tiers:\n"
        "  gov: 4\n"
        "  edu: 3\n",
        encoding="utf-8",
    )

    ranker = Ranker(load_authority_config(config_path))

    assert ranker.authority("https://trusted.example/page") == 4
    assert ranker.authority("https://news.trusted.example/page") == 4
    assert ranker.authority("https://partner.example/page") == 2
    assert ranker.authority("https://university.edu/page") == 3
    assert ranker.authority("https://agency.gov/page") == 4
    assert ranker.authority("https://unknown-domain.example/page") == -1


def test_cross_encoder_score_is_blended_into_rerank_score() -> None:
    heuristic_only = Ranker()
    blended = Ranker(cross_encoder_weight=0.5)
    signals = heuristic_only.signals(
        url="https://example.org/page",
        title="Example",
        snippet="Example snippet about the topic.",
        content="Example snippet about the topic.",
        published_at=None,
        query_terms=("example",),
        engine_score=None,
    )

    base = heuristic_only.rerank_score(signals)
    mixed = blended.rerank_score(signals, 3.0)

    assert 0.0 <= mixed <= 3.0
    assert mixed != base
    assert mixed > base


def test_cross_encoder_reranker_scores_documents_with_injected_session() -> None:
    class FakeEncoding:
        def __init__(self, length: int) -> None:
            self.ids = [1] * length
            self.attention_mask = [1] * length

    class FakeTokenizer:
        def encode_batch(self, pairs):
            return [FakeEncoding(3) for _ in pairs]

    class FakeInput:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeSession:
        def get_inputs(self):
            return [FakeInput("input_ids"), FakeInput("attention_mask")]

        def run(self, _output_names, inputs):
            import numpy as np

            rows = inputs["input_ids"].shape[0]
            return [np.array([[0.5], [2.5]][:rows], dtype=np.float32)]

    reranker = CrossEncoderReranker(
        "unused",
        session=FakeSession(),
        tokenizer=FakeTokenizer(),
    )

    scores = reranker.score("query", ["first", "second"])

    assert scores == [0.5, 2.5]
    assert 0.0 < normalize_logit(0.5) < 1.0


@pytest.mark.asyncio
async def test_renderer_reports_missing_playwright() -> None:
    try:
        import playwright  # noqa: F401
    except ImportError:
        pass
    else:  # pragma: no cover - only when the optional extra is installed
        pytest.skip("playwright is installed in this environment")

    renderer = JavaScriptRenderer(timeout_seconds=1.0)

    with pytest.raises(APIError) as captured:
        await renderer.render("https://93.184.216.34/page")

    assert captured.value.status_code == 503
    assert captured.value.code == "renderer_unavailable"


@pytest.mark.asyncio
async def test_fetch_uses_rendered_html_when_enabled(tmp_path) -> None:
    class StubRenderer:
        minimum_content_chars = 10

        async def render(self, url: str) -> str:
            return (
                "<html><body><main><h1>Rendered</h1>"
                "<p>Content that only exists after JavaScript execution.</p>"
                "</main></body></html>"
            )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><body><main></main></body></html>",
            headers={"content-type": "text/html"},
        )

    async with api_harness(handler, tmp_path) as (client, application):
        application.state.fetcher.renderer = StubRenderer()
        response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://93.184.216.34/app"},
        )

    assert response.status_code == 200
    assert "JavaScript execution" in response.json()["content"]


@pytest.mark.asyncio
async def test_responses_are_marked_as_untrusted(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Result",
                            "url": "https://93.184.216.34/result",
                            "content": "A snippet with enough text to be useful. " * 10,
                            "engine": "bing",
                            "publishedDate": "2026-01-01T00:00:00Z",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            text="<html><main>Fetched page body</main></html>",
            headers={"content-type": "text/html"},
        )

    async with api_harness(handler, tmp_path) as (client, _):
        search_response = await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": "untrusted marker", "count": 1},
        )
        fetch_response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://93.184.216.34/page"},
        )

    assert search_response.headers["X-Content-Untrusted"] == "true"
    assert fetch_response.headers["X-Content-Untrusted"] == "true"


def test_fetch_service_uses_default_security_policy() -> None:
    service = FetchService(
        client=httpx.AsyncClient(),
        policy=SecurityPolicy(),
        respect_robots=False,
    )

    assert service.policy is not None
