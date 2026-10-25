from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from app.content import _remove_unsafe_nodes, extract_page
from tests.helpers import JSON_HEADERS, api_harness, search_result, searxng_response


def test_unsafe_node_cleanup_handles_missing_tag_attributes() -> None:
    soup = BeautifulSoup("<main><p>Usable text</p></main>", "html.parser")
    paragraph = soup.find("p")
    assert paragraph is not None
    paragraph.attrs = None

    _remove_unsafe_nodes(soup)

    assert "Usable text" in soup.get_text()


def test_main_content_extraction_excludes_page_chrome() -> None:
    page = extract_page(
        """
        <html>
          <head><title>Research article</title></head>
          <body>
            <nav>Home Products Pricing</nav>
            <div id="cookie-banner">Accept all cookies</div>
            <main>
              <h1>Research article</h1>
              <p>The main finding is supported by reproducible measurements.</p>
            </main>
            <aside><ul><li>Related one</li><li>Related two</li></ul></aside>
            <footer>Copyright</footer>
          </body>
        </html>
        """
    )

    assert "main finding" in page.content
    assert "Home Products Pricing" not in page.content
    assert "Accept all cookies" not in page.content
    assert "Related one" not in page.content
    assert "Copyright" not in page.content


async def test_search_snippet_and_summary_exclude_page_chrome(tmp_path) -> None:
    def handler(request):
        if request.url.path == "/search":
            return searxng_response(
                [
                    search_result(
                        title="Research article",
                        url="https://93.184.216.34/article",
                        content="Short provider snippet.",
                    )
                ]
            )
        return httpx.Response(
            200,
            text="""
            <html>
              <head><title>Research article</title></head>
              <body>
                <nav>Home Products Pricing</nav>
                <div class="cookie-consent">Accept all cookies</div>
                <main><h1>Research article</h1>
                  <p>The main finding is supported by reproducible measurements and detailed context.</p>
                </main>
                <ul class="link-list"><li>Unrelated link one</li><li>Unrelated link two</li></ul>
              </body>
            </html>
            """,
            headers={"content-type": "text/html; charset=utf-8"},
        )

    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": "research finding", "need_content": True},
        )

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert "main finding" in result["snippet"]
    assert "main finding" in result["summary"]
    for chrome in ("Home Products Pricing", "Accept all cookies", "Unrelated link one"):
        assert chrome not in result["snippet"]
        assert chrome not in result["summary"]
        assert chrome not in result["content"]
