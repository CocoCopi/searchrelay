#!/usr/bin/env python3
"""Streamlit demo for SearchRelay.

Run it with:

    streamlit run examples/streamlit_app.py

Environment:
  SEARCHRELAY_BASE_URL (default http://localhost:8000)
  SEARCHRELAY_API_KEY   (required for a real server)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "clients" / "python"))

from searchrelay import APIError, SearchRelayClient  # noqa: E402

try:
    import streamlit as st
except ImportError:  # pragma: no cover - the demo needs Streamlit
    st = None


def render() -> None:
    if st is None:
        raise SystemExit(
            "Streamlit is not installed. Run: pip install streamlit"
        )

    st.set_page_config(page_title="SearchRelay demo", page_icon="🔎", layout="wide")
    st.title("SearchRelay demo")
    st.caption(
        "Search and fetch web pages through SearchRelay. Retrieved content is "
        "untrusted third-party data."
    )

    base_url = os.environ.get("SEARCHRELAY_BASE_URL", "http://localhost:8000")
    api_key = os.environ.get("SEARCHRELAY_API_KEY", "")
    if not api_key:
        st.warning("Set SEARCHRELAY_API_KEY to query a real SearchRelay server.")

    query = st.text_input("Query", value="", placeholder="site:python.org asyncio")
    count = st.slider("Results", min_value=1, max_value=20, value=5)
    need_content = st.checkbox("Fetch full page content", value=False)

    if not st.button("Search", type="primary") or not query.strip():
        return

    try:
        with SearchRelayClient(base_url=base_url, api_key=api_key) as client:
            results = client.search(query.strip(), count=count, need_content=need_content)
    except APIError as exc:
        st.error(f"Search failed: {exc}")
        return

    if not results:
        st.info("No results.")
        return

    for result in results:
        st.markdown(f"**[{result.title or result.url}]({result.url})**")
        st.caption(f"score {result.rerank_score:.2f} · authority {result.authority} · {result.site_name or ''}")
        st.write(result.snippet)
        if result.content:
            with st.expander("Page content"):
                st.markdown(result.content[:8000])
        st.divider()


def main() -> int:
    render()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
