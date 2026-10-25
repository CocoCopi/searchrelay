from __future__ import annotations

import socket

import httpx
import pytest

from tests.helpers import JSON_HEADERS, api_harness


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/secret",
        "http://10.0.0.1/secret",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/",
        "http://localhost/",
        "http://service.internal/",
    ],
)
async def test_private_and_metadata_targets_are_blocked(tmp_path, url):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="should never be read")

    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": url},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "security_policy_blocked"


@pytest.mark.asyncio
async def test_hostname_is_resolved_and_connection_is_pinned(tmp_path, monkeypatch):
    def fake_getaddrinfo(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ]

    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url_host"] = request.url.host
        observed["host_header"] = request.headers.get("host")
        return httpx.Response(
            200,
            text="<html><title>Pinned</title><main>Public page</main></html>",
            headers={"content-type": "text/html"},
        )

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://example.com/page"},
        )

    assert response.status_code == 200
    assert observed == {"url_host": "93.184.216.34", "host_header": "example.com"}


@pytest.mark.asyncio
async def test_hostname_with_any_private_address_is_blocked(tmp_path, monkeypatch):
    def fake_getaddrinfo(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="should never be read")

    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://mixed.example/page"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "security_policy_blocked"


@pytest.mark.asyncio
async def test_url_userinfo_is_rejected(tmp_path):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="should never be read")

    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://user:password@example.com/page"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_url"


@pytest.mark.asyncio
async def test_redirect_to_private_address_is_blocked(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        return httpx.Response(200, text="public")

    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://93.184.216.34/redirect"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "security_policy_blocked"


@pytest.mark.asyncio
async def test_unsupported_content_type_returns_404_and_is_free(tmp_path):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"binary", headers={"content-type": "image/png"})

    async with api_harness(handler, tmp_path) as (client, application):
        before = application.state.billing.get_balance("account-test")
        response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://93.184.216.34/image.png"},
        )
        after = application.state.billing.get_balance("account-test")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unsupported_content_type"
    assert before == after


@pytest.mark.asyncio
async def test_dead_url_returns_404_and_is_free(tmp_path):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="missing")

    async with api_harness(handler, tmp_path) as (client, application):
        before = application.state.billing.get_balance("account-test")
        response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://93.184.216.34/missing"},
        )
        after = application.state.billing.get_balance("account-test")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_usable_content"
    assert before == after


@pytest.mark.asyncio
async def test_robots_denial_returns_404(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: DeepSeekBot\nDisallow: /private\n",
                headers={"content-type": "text/plain"},
            )
        return httpx.Response(200, text="private content")

    async with api_harness(handler, tmp_path, respect_robots=True) as (client, _):
        response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://93.184.216.34/private/page"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_usable_content"


@pytest.mark.asyncio
async def test_body_timeout_returns_408(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://93.184.216.34/slow"},
        )

    assert response.status_code == 408
    assert response.json()["error"]["code"] == "body_timeout"


@pytest.mark.asyncio
async def test_fetched_body_over_limit_returns_413(tmp_path):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><main>" + ("x" * 3000) + "</main></html>",
            headers={"content-type": "text/html"},
        )

    async with api_harness(handler, tmp_path, fetch_max_bytes=1024) as (client, _):
        response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://93.184.216.34/large"},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "body_too_large"


@pytest.mark.asyncio
async def test_successful_fetch_is_billable(tmp_path):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><title>Page</title><main>Usable content</main></html>",
            headers={"content-type": "text/html"},
        )

    async with api_harness(handler, tmp_path) as (client, application):
        before = application.state.billing.get_balance("account-test")
        response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://93.184.216.34/page"},
        )
        after = application.state.billing.get_balance("account-test")

    assert response.status_code == 200
    assert before - after == 750
