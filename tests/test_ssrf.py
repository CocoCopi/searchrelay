from __future__ import annotations

import socket

import httpx
import pytest

from app.errors import APIError
from app.security import (
    URLValidationError,
    resolve_public_addresses,
    validate_url,
)
from tests.helpers import JSON_HEADERS, api_harness


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.org/file",
        "gopher://example.org/file",
        "data:text/plain,hello",
    ],
)
def test_non_http_schemes_are_rejected(url: str) -> None:
    with pytest.raises(URLValidationError):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org:8080/",
        "https://example.org:8443/",
        "http://example.org:22/",
        "https://example.org:443:80/",
    ],
)
def test_non_standard_ports_are_rejected(url: str) -> None:
    with pytest.raises(URLValidationError):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://localhost/",
        "http://service.internal/",
        "http://printer.local/",
    ],
)
def test_non_public_ip_literals_are_blocked(url: str) -> None:
    with pytest.raises(APIError) as captured:
        validate_url(url)

    assert captured.value.status_code == 403
    assert captured.value.code == "security_policy_blocked"


@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/",
        "http://0x7f000001/",
        "http://017700000001/",
    ],
)
@pytest.mark.asyncio
async def test_ambiguous_or_hostile_ip_forms_do_not_bypass_validation(
    url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(*args: object, **kwargs: object):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises((APIError, URLValidationError)):
        validate_url(url)


@pytest.mark.asyncio
async def test_hostname_resolving_to_a_private_address_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(*args: object, **kwargs: object):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(APIError) as captured:
        await resolve_public_addresses("127.0.0.1.nip.io")

    assert captured.value.status_code == 403


@pytest.mark.asyncio
async def test_dns_rebinding_cannot_replace_the_pinned_public_address(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    observed_hosts: list[str] = []

    def fake_getaddrinfo(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    def handler(request: httpx.Request) -> httpx.Response:
        observed_hosts.append(request.url.host)
        return httpx.Response(
            200,
            text="<html><main>Public content</main></html>",
            headers={"content-type": "text/html"},
        )

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://rebind.example/page"},
        )

    assert response.status_code == 200
    assert observed_hosts == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_redirect_to_a_disallowed_port_is_not_followed(tmp_path) -> None:
    observed_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_paths.append(request.url.path)
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8080/private"})

    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://93.184.216.34/redirect"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_usable_content"
    assert observed_paths == ["/redirect"]


@pytest.mark.asyncio
async def test_domain_allowlist_and_blocklist_are_enforced(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(*args: object, **kwargs: object):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="public content")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    async with api_harness(
        handler,
        tmp_path,
        allowed_domains_csv="allowed.example",
    ) as (client, _):
        allowed = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://sub.allowed.example/page"},
        )
        denied = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://other.example/page"},
        )

    assert allowed.status_code == 200
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "security_policy_blocked"
