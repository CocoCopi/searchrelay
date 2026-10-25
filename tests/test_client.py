from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "clients" / "python"))

from searchfetch import APIError, SearchFetchClient  # noqa: E402


def test_python_client_retries_server_error_then_succeeds(monkeypatch) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                500,
                json={"error": {"message": "temporary", "type": "invalid_request_error", "param": None, "code": "internal_error"}},
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "rerank_score": 1.5,
                        "title": "Result",
                        "url": "https://example.com",
                        "snippet": "Snippet",
                        "summary": "Summary",
                        "content": "",
                        "site_name": "example.com",
                        "site_icon": None,
                        "date": None,
                        "authority": 2,
                    }
                ]
            },
        )

    monkeypatch.setattr("searchfetch.client._sleep_before_retry", lambda *_: None)
    client = SearchFetchClient(
        "http://api.test",
        "key",
        max_retries=2,
        transport=httpx.MockTransport(handler),
    )
    results = client.search("query")

    assert calls == 2
    assert len(results) == 1
    assert results[0].title == "Result"


def test_python_client_does_not_retry_non_retryable_4xx() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            400,
            json={"error": {"message": "bad", "type": "invalid_request_error", "param": None, "code": "invalid_request"}},
        )

    client = SearchFetchClient(
        "http://api.test",
        "key",
        max_retries=3,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(APIError) as exc_info:
        client.search("query")

    assert calls == 1
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "invalid_request"
