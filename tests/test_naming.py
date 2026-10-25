"""Regression tests for the SearchRelay naming convention.

The project was renamed from "Search & Fetch" to "SearchRelay" before the
v0.1.0 release. These tests lock the new names in place and keep the
deprecated ``SF_`` environment prefix working as a fallback.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]

# Files that are allowed to mention the historical names on purpose.
ALLOWED_LEGACY_MENTIONS = {
    Path("CHANGELOG.md"),
    Path("docs/upgrade.md"),
    Path("README.md"),
    Path("tests/test_naming.py"),
}

LEGACY_MARKERS = ("search" + "fetch", "Search" + "Fetch", "SEARCH" + "FETCH")


def _tracked_source_files() -> list[Path]:
    files: list[Path] = []
    for directory in ("app", "scripts", "clients", "examples", "benchmarks", "tests", "config"):
        for path in (REPO_ROOT / directory).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".toml", ".yaml", ".yml", ".go", ".ts", ".md"}:
                files.append(path.relative_to(REPO_ROOT))
    for name in (".env.example", "Dockerfile", "docker-compose.yml"):
        files.append(Path(name))
    return sorted(files)


def test_source_tree_uses_the_searchrelay_name() -> None:
    offenders: list[str] = []
    for relative_path in _tracked_source_files():
        if relative_path in ALLOWED_LEGACY_MENTIONS:
            continue
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8", errors="ignore")
        for marker in LEGACY_MARKERS:
            if marker in text:
                offenders.append(f"{relative_path}: {marker}")
    assert not offenders, "legacy naming found: " + ", ".join(offenders)


def test_package_metadata_matches_the_release_version() -> None:
    root_metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    python_metadata = tomllib.loads(
        (REPO_ROOT / "clients" / "python" / "pyproject.toml").read_text(encoding="utf-8")
    )
    typescript_metadata = (
        REPO_ROOT / "clients" / "typescript" / "package.json"
    ).read_text(encoding="utf-8")

    assert root_metadata["project"]["name"] == "searchrelay"
    assert root_metadata["project"]["version"] == "0.1.0"
    assert python_metadata["project"]["name"] == "searchrelay-python"
    assert python_metadata["project"]["version"] == "0.1.0"
    assert python_metadata["tool"]["setuptools"]["packages"] == ["searchrelay"]
    assert '"name": "searchrelay-sdk"' in typescript_metadata


def test_service_identifies_itself_as_searchrelay() -> None:
    settings = Settings(api_keys_json={"sk-test": "account-test"})
    assert settings.service_version == "0.1.0"
    assert settings.user_agent == "SearchRelayBot/1.0"
    assert settings.otel_service_name == "searchrelay"
    assert settings.redis_prefix == "searchrelay"
    assert settings.database_path == Path("data/searchrelay.db")


def test_sr_prefix_is_the_primary_environment_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SF_SEARCH_QPS", raising=False)
    monkeypatch.setenv("SR_SEARCH_QPS", "17")
    settings = Settings(api_keys_json={"sk-test": "account-test"})
    assert settings.search_qps == 17


def test_legacy_sf_prefix_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SR_SEARCH_QPS", raising=False)
    monkeypatch.setenv("SF_SEARCH_QPS", "19")
    settings = Settings(api_keys_json={"sk-test": "account-test"})
    assert settings.search_qps == 19


def test_sr_prefix_wins_over_the_legacy_sf_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SR_SEARCH_QPS", "17")
    monkeypatch.setenv("SF_SEARCH_QPS", "19")
    settings = Settings(api_keys_json={"sk-test": "account-test"})
    assert settings.search_qps == 17


def test_legacy_json_key_mapping_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SR_API_KEYS_JSON", raising=False)
    monkeypatch.setenv("SF_API_KEYS_JSON", '{"sk-legacy": "account-legacy"}')
    settings = Settings()
    assert settings.effective_api_keys() == {"sk-legacy": "account-legacy"}


