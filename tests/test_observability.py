from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.metrics import Metrics
from app.observability import JsonLogFormatter, configure_logging
from tests.helpers import AUTH_HEADERS, JSON_HEADERS, api_harness, search_result


@pytest.mark.asyncio
async def test_metrics_endpoint_exposes_prometheus_metrics(tmp_path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [search_result()]})

    async with api_harness(handler, tmp_path) as (client, _):
        await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": "fastapi", "count": 2},
        )
        response = await client.get("/metrics")

    assert response.status_code == 200
    body = response.text
    assert "searchrelay_http_requests_total" in body
    assert 'route="/v1/tools/search"' in body
    assert "searchrelay_build_info" in body


@pytest.mark.asyncio
async def test_requests_receive_a_request_id_header(tmp_path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.get("/healthz", headers={"X-Request-Id": "req-123"})

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "req-123"


@pytest.mark.asyncio
async def test_failed_requests_are_counted_with_their_status(tmp_path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    async with api_harness(handler, tmp_path) as (client, _):
        await client.post(
            "/v1/tools/search",
            headers={"Authorization": "Bearer wrong", "Content-Type": "application/json"},
            json={"query": "fastapi"},
        )
        response = await client.get("/metrics")

    assert 'status="401"' in response.text


def test_json_log_formatter_emits_one_json_object_per_record() -> None:
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="searchrelay.http",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="http_request",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-1"
    record.status = 200

    payload = json.loads(formatter.format(record))

    assert payload["message"] == "http_request"
    assert payload["level"] == "info"
    assert payload["logger"] == "searchrelay.http"
    assert payload["request_id"] == "req-1"
    assert payload["status"] == 200
    assert payload["timestamp"].endswith("Z")


def test_configure_logging_installs_a_json_handler(capsys) -> None:
    configure_logging(level="INFO", json_logs=True)
    logging.getLogger("searchrelay.test").info("hello", extra={"request_id": "req-2"})

    captured = capsys.readouterr().out.strip().splitlines()
    payload = json.loads(captured[-1])
    assert payload["message"] == "hello"
    assert payload["request_id"] == "req-2"


def test_metrics_observer_records_provider_calls() -> None:
    metrics = Metrics()
    metrics.observe_provider(provider="searxng", outcome="success", duration=0.25)

    body, content_type = metrics.render()
    assert "text/plain" in content_type
    assert b'searchrelay_provider_requests_total{outcome="success",provider="searxng"} 1.0' in body
