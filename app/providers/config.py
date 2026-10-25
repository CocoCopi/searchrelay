from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings


KNOWN_PROVIDERS = {"searxng", "brave", "serper", "tavily", "exa", "local"}
DEFAULT_API_KEY_ENV = {
    "brave": "BRAVE_SEARCH_API_KEY",
    "serper": "SERPER_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "exa": "EXA_API_KEY",
}
DEFAULT_BASE_URLS = {
    "brave": "https://api.search.brave.com/res/v1",
    "serper": "https://google.serper.dev/search",
    "tavily": "https://api.tavily.com/search",
    "exa": "https://api.exa.ai/search",
}


@dataclass(frozen=True)
class ProviderRuntimeConfig:
    name: str
    enabled: bool = True
    api_key: str = field(default="", repr=False)
    base_url: str = ""
    timeout_seconds: float = 5.0
    requests_per_minute: int = 60
    max_concurrency: int = 2
    max_results: int = 20


def load_provider_configs(settings: Settings) -> list[ProviderRuntimeConfig]:
    payload = _load_config_file(settings.provider_config_file)
    priorities = _priorities(payload, settings.provider_priorities_csv)
    entries = payload.get("providers", {})
    if not isinstance(entries, dict):
        raise RuntimeError("SearchRelay provider configuration error: 'providers' must be an object")

    configs: list[ProviderRuntimeConfig] = []
    for name in priorities:
        if name not in KNOWN_PROVIDERS:
            raise RuntimeError(
                f"SearchRelay provider configuration error: unknown provider '{name}'"
            )
        raw = entries.get(name, {})
        if not isinstance(raw, dict):
            raise RuntimeError(
                f"SearchRelay provider configuration error: provider '{name}' must be an object"
            )
        enabled = _boolean(raw.get("enabled", True), name, "enabled")
        if not enabled:
            continue
        if name == "searxng":
            configs.append(
                ProviderRuntimeConfig(
                    name=name,
                    enabled=True,
                    base_url=str(raw.get("base_url") or settings.upstream_url).rstrip("/"),
                    timeout_seconds=_number(
                        raw.get("timeout_seconds", settings.upstream_timeout_seconds),
                        name,
                        "timeout_seconds",
                    ),
                    requests_per_minute=_integer(
                        raw.get("requests_per_minute", 60),
                        name,
                        "requests_per_minute",
                    ),
                    max_concurrency=_integer(
                        raw.get("max_concurrency", 2),
                        name,
                        "max_concurrency",
                    ),
                    max_results=_integer(
                        raw.get("max_results", 20),
                        name,
                        "max_results",
                    ),
                )
            )
            continue
        if name == "local":
            configs.append(
                ProviderRuntimeConfig(
                    name=name,
                    enabled=True,
                    base_url=str(raw.get("base_url") or ""),
                    timeout_seconds=_number(
                        raw.get("timeout_seconds", 5.0),
                        name,
                        "timeout_seconds",
                    ),
                    requests_per_minute=_integer(
                        raw.get("requests_per_minute", 60),
                        name,
                        "requests_per_minute",
                    ),
                    max_concurrency=_integer(
                        raw.get("max_concurrency", 2),
                        name,
                        "max_concurrency",
                    ),
                    max_results=_integer(
                        raw.get("max_results", 20),
                        name,
                        "max_results",
                    ),
                )
            )
            continue

        api_key_env = str(raw.get("api_key_env") or DEFAULT_API_KEY_ENV[name])
        api_key = os.getenv(api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(
                "SearchRelay provider configuration error: "
                f"provider '{name}' requires {api_key_env}"
            )
        configs.append(
            ProviderRuntimeConfig(
                name=name,
                enabled=True,
                api_key=api_key,
                base_url=str(raw.get("base_url") or DEFAULT_BASE_URLS[name]).rstrip("/"),
                timeout_seconds=_number(
                    raw.get("timeout_seconds", 5.0),
                    name,
                    "timeout_seconds",
                ),
                requests_per_minute=_integer(
                    raw.get("requests_per_minute", 60),
                    name,
                    "requests_per_minute",
                ),
                max_concurrency=_integer(
                    raw.get("max_concurrency", 2),
                    name,
                    "max_concurrency",
                ),
                max_results=_integer(
                    raw.get("max_results", 20),
                    name,
                    "max_results",
                ),
            )
        )

    if not configs:
        raise RuntimeError(
            "SearchRelay provider configuration error: at least one provider must be enabled"
        )
    return configs


def _load_config_file(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    expanded = path.expanduser()
    if not expanded.exists():
        raise RuntimeError(
            f"SearchRelay provider configuration error: file not found: {expanded}"
        )
    try:
        payload = json.loads(expanded.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"SearchRelay provider configuration error: unable to read {expanded}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            "SearchRelay provider configuration error: provider file must contain an object"
        )
    return payload


def _priorities(payload: dict[str, Any], fallback_csv: str) -> list[str]:
    raw = payload.get("priorities")
    if raw is None:
        raw = [item.strip() for item in fallback_csv.split(",") if item.strip()]
    if not isinstance(raw, list) or not raw:
        raise RuntimeError(
            "SearchRelay provider configuration error: 'priorities' must be a non-empty array"
        )
    priorities: list[str] = []
    for item in raw:
        name = str(item).strip().casefold()
        if name and name not in priorities:
            priorities.append(name)
    return priorities


def _boolean(value: object, provider: str, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeError(
            "SearchRelay provider configuration error: "
            f"provider '{provider}' field '{field_name}' must be boolean"
        )
    return value


def _number(value: object, provider: str, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise RuntimeError(
            "SearchRelay provider configuration error: "
            f"provider '{provider}' field '{field_name}' must be a positive number"
        )
    return float(value)


def _integer(value: object, provider: str, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuntimeError(
            "SearchRelay provider configuration error: "
            f"provider '{provider}' field '{field_name}' must be a positive integer"
        )
    return value
