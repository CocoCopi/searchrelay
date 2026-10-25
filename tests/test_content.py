from __future__ import annotations

from bs4 import BeautifulSoup

from app.content import _remove_unsafe_nodes


def test_unsafe_node_cleanup_handles_missing_tag_attributes() -> None:
    soup = BeautifulSoup("<main><p>Usable text</p></main>", "html.parser")
    paragraph = soup.find("p")
    assert paragraph is not None
    paragraph.attrs = None

    _remove_unsafe_nodes(soup)

    assert "Usable text" in soup.get_text()
