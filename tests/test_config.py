"""Configuration validation regression tests."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from tests.helpers import api_harness


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"api_keys_json": {"sk-test": "account-test"}}
    values.update(overrides)
    return Settings(**values)


def test_empty_cache_database_path_disables_the_persistent_tier() -> None:
    settings = _settings(cache_database_path="")

    assert settings.cache_database_path is None


def test_empty_database_path_is_rejected() -> None:
    with pytest.raises(ValidationError, match="database_path must point at a writable file"):
        _settings(database_path="")


def test_empty_local_index_path_is_rejected() -> None:
    with pytest.raises(ValidationError, match="local_index_path must point at a writable file"):
        _settings(local_index_path="")


def test_cache_database_path_pointing_at_a_directory_is_rejected() -> None:
    with pytest.raises(ValidationError, match="cache_database_path must point at a writable file"):
        _settings(cache_database_path=".")


@pytest.mark.asyncio
async def test_search_succeeds_when_the_cache_path_is_empty(tmp_path: Path) -> None:
    """An empty cache path used to raise a 500 on every search request."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "FastAPI",
                        "url": "https://fastapi.tiangolo.com/",
                        "content": "FastAPI is a web framework. " * 20,
                        "engine": "mock",
                    }
                ]
            },
        )

    async with api_harness(handler, tmp_path, cache_database_path="") as (client, _):
        response = await client.post(
            "/v1/tools/search",
            headers={"Authorization": "Bearer sk-test"},
            json={"query": "fastapi"},
        )

    assert response.status_code == 200
    assert response.json()["results"]
