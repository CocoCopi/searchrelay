from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests.helpers import api_harness


@pytest.mark.asyncio
async def test_healthz_checks_backend_reachability(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(200, text="OK")
        return httpx.Response(200, json={"results": []})

    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_healthz_and_readyz_report_unavailable_backend(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(503)
        return httpx.Response(200, json={"results": []})

    async with api_harness(handler, tmp_path) as (client, _):
        health = await client.get("/healthz")
        readiness = await client.get("/readyz")

    assert health.status_code == 503
    assert health.json() == {"status": "unavailable"}
    assert readiness.status_code == 503
    assert readiness.json() == {"status": "not_ready"}


@pytest.mark.asyncio
async def test_startup_fails_fast_when_api_key_configuration_is_missing(monkeypatch) -> None:
    monkeypatch.setenv("SF_API_KEYS_JSON", "")
    settings = Settings()
    application = create_app(settings)

    with pytest.raises(RuntimeError, match="SF_API_KEYS_JSON or SF_KEYS_FILE"):
        async with application.router.lifespan_context(application):
            pass


def test_deployment_files_do_not_contain_hardcoded_credentials() -> None:
    root = Path(__file__).resolve().parents[1]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    searxng = (root / "deploy" / "searxng" / "settings.yml").read_text(encoding="utf-8")

    assert "sk-local-dev" not in compose
    assert "local-development-only-change-this-secret" not in searxng
    assert "__SEARXNG_SECRET__" in searxng
    assert "SEARXNG_SECRET" in compose
