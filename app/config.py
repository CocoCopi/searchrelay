from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Environment variables use the ``SF_`` prefix. Dictionary values are JSON
    encoded when supplied through the environment.
    """

    model_config = SettingsConfigDict(
        env_prefix="SF_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    upstream_url: str = "http://searxng:8080"
    upstream_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    engine_name: str = "searxng"

    api_keys_json: dict[str, str] = Field(default_factory=dict)
    initial_balances_json: dict[str, int] = Field(default_factory=dict)
    keys_file: Path | None = None

    database_path: Path = Path("data/searchfetch.db")
    price_micros_per_request: int = Field(default=750, ge=0)
    search_qps: int = Field(default=100, ge=1)
    fetch_qps: int = Field(default=25, ge=1)
    fetch_domain_qps: int = Field(default=5, ge=1)

    max_request_body_bytes: int = Field(default=64 * 1024, ge=1024)
    fetch_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)
    fetch_timeout_seconds: float = Field(default=12.0, gt=0, le=120)
    fetch_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    fetch_max_redirects: int = Field(default=5, ge=0, le=10)
    fetch_concurrency: int = Field(default=5, ge=1, le=20)
    respect_robots: bool = True
    user_agent: str = "DeepSeekBot/1.0"

    blocked_domains_csv: str = ""
    allowed_domains_csv: str = ""

    @field_validator("upstream_url")
    @classmethod
    def validate_upstream_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("upstream_url must use http or https")
        return value

    @field_validator("database_path")
    @classmethod
    def validate_database_path(cls, value: Path) -> Path:
        return value.expanduser()

    def _read_keys_file(self) -> tuple[dict[str, str], dict[str, int]]:
        if not self.keys_file:
            return {}, {}
        path = self.keys_file.expanduser()
        if not path.exists():
            return {}, {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Unable to read API keys file {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("API keys file must contain a JSON object")
        keys = payload.get("keys", {})
        balances = payload.get("balances", {})
        if not isinstance(keys, dict) or not isinstance(balances, dict):
            raise ValueError("API keys file 'keys' and 'balances' values must be objects")
        return {str(k): str(v) for k, v in keys.items()}, {
            str(k): int(v) for k, v in balances.items()
        }

    def effective_api_keys(self) -> dict[str, str]:
        file_keys, _ = self._read_keys_file()
        file_keys.update(self.api_keys_json)
        return file_keys

    def effective_initial_balances(self) -> dict[str, int]:
        _, file_balances = self._read_keys_file()
        file_balances.update(self.initial_balances_json)
        return file_balances

    def blocked_domains(self) -> set[str]:
        return _csv_set(self.blocked_domains_csv)

    def allowed_domains(self) -> set[str]:
        return _csv_set(self.allowed_domains_csv)

    def request_limits(self) -> dict[str, Any]:
        return {
            "max_request_body_bytes": self.max_request_body_bytes,
            "fetch_max_bytes": self.fetch_max_bytes,
            "fetch_timeout_seconds": self.fetch_timeout_seconds,
            "fetch_connect_timeout_seconds": self.fetch_connect_timeout_seconds,
            "fetch_max_redirects": self.fetch_max_redirects,
            "fetch_concurrency": self.fetch_concurrency,
            "respect_robots": self.respect_robots,
            "user_agent": self.user_agent,
        }


def _csv_set(value: str) -> set[str]:
    return {
        item.strip().lower().rstrip(".")
        for item in value.split(",")
        if item.strip()
    }
