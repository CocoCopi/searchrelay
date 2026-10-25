"""Search-quality benchmark core.

The harness measures provider result ordering (and, separately, reciprocal-rank
fusion) against domain-level relevance judgments.  It deliberately does not
fetch result pages: this keeps provider comparisons focused on retrieval and
avoids charging for page fetches during a benchmark run.

The public API contract remains unchanged; this module is an offline operator
tool.
"""

from __future__ import annotations

import hashlib
import json
import math
import shlex
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

import httpx

from app.config import Settings
from app.operators import parse_query
from app.providers.base import ProviderSearchResult, SearchCandidate, SearchProvider
from app.providers.config import load_provider_configs
from app.providers.factory import build_provider
from app.providers.federation import FederatedSearchProvider
from app.providers.reliability import ReliabilityConfig, ResilientProvider


@dataclass(frozen=True)
class GoldenQuery:
    id: str
    category: str
    query: str
    relevance: Mapping[str, int]


@dataclass(frozen=True)
class QueryMeasurement:
    query_id: str
    category: str
    latency_ms: float
    ndcg_at_10: float
    success: bool
    error: str | None = None
    result_urls: tuple[str, ...] = ()
    providers_used: tuple[str, ...] = ()


@dataclass
class RunSummary:
    mode: str
    provider: str
    total_queries: int
    successful_queries: int
    failed_queries: int
    failure_rate: float
    ndcg_at_10: float
    latency_ms_p50: float
    latency_ms_p95: float
    categories: dict[str, dict[str, float | int]] = field(default_factory=dict)
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BenchmarkProvider(Protocol):
    name: str

    async def search(
        self,
        parsed: Any,
        *,
        count: int,
        localization: str,
    ) -> ProviderSearchResult: ...


def load_golden_set(path: Path | str) -> list[GoldenQuery]:
    """Load and strictly validate the JSONL golden set."""

    source = Path(path)
    queries: list[GoldenQuery] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{source}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{source}:{line_number}: each line must be a JSON object")
        query_id = str(payload.get("id") or "").strip()
        category = str(payload.get("category") or "").strip()
        query = str(payload.get("query") or "").strip()
        relevance = payload.get("relevance")
        if not query_id or not category or not query:
            raise ValueError(f"{source}:{line_number}: id, category, and query are required")
        if query_id in seen_ids:
            raise ValueError(f"{source}:{line_number}: duplicate query id {query_id!r}")
        if not isinstance(relevance, dict) or not relevance:
            raise ValueError(f"{source}:{line_number}: relevance must be a non-empty object")
        judgments: dict[str, int] = {}
        for domain, grade in relevance.items():
            if not isinstance(domain, str) or not domain.strip():
                raise ValueError(f"{source}:{line_number}: relevance domains must be strings")
            if isinstance(grade, bool) or not isinstance(grade, int) or grade < 0:
                raise ValueError(
                    f"{source}:{line_number}: relevance grades must be non-negative integers"
                )
            judgments[domain.casefold().rstrip(".")] = grade
        seen_ids.add(query_id)
        queries.append(
            GoldenQuery(
                id=query_id,
                category=category,
                query=query,
                relevance=judgments,
            )
        )
    if not queries:
        raise ValueError(f"{source}: the golden set is empty")
    return queries


def golden_set_digest(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def domain_grade(url: str, relevance: Mapping[str, int]) -> int:
    """Return the judgment for a URL's domain.

    The most specific matching judged domain wins.  For example, an explicit
    judgment for ``ec.europa.eu`` is preferred over a parent ``europa.eu``
    judgment.  Ties at the same specificity use the highest grade.
    """

    try:
        host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return 0
    matches = [
        (len(domain), grade)
        for domain, grade in relevance.items()
        if host == domain or host.endswith(f".{domain}")
    ]
    if not matches:
        return 0
    longest = max(length for length, _ in matches)
    return max(grade for length, grade in matches if length == longest)


def ndcg_at_k(
    urls: Sequence[str],
    relevance: Mapping[str, int],
    *,
    k: int = 10,
) -> float:
    """Compute nDCG@k using exponential gains for domain judgments."""

    if k <= 0:
        raise ValueError("k must be positive")
    dcg = sum(
        (2**domain_grade(url, relevance) - 1) / math.log2(rank + 1)
        for rank, url in enumerate(urls[:k], start=1)
    )
    ideal_grades = sorted(relevance.values(), reverse=True)[:k]
    idcg = sum(
        (2**grade - 1) / math.log2(rank + 1)
        for rank, grade in enumerate(ideal_grades, start=1)
    )
    return dcg / idcg if idcg > 0 else 0.0


def percentile(values: Sequence[float], quantile: float) -> float:
    """Return a linearly interpolated percentile."""

    if not values:
        return 0.0
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between 0 and 1")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


class SyntheticProvider:
    """A deterministic provider used only to self-test the harness.

    It derives URLs from the golden judgments, so its quality score is 1.0 by
    construction.  Synthetic rows are never presented as live provider
    evidence.
    """

    def __init__(self, name: str, queries: Sequence[GoldenQuery]) -> None:
        self.name = name
        self._responses = {
            query.query: tuple(
                SearchCandidate(
                    title=f"Synthetic result for {domain}",
                    url=f"https://{domain}/synthetic-result",
                    snippet="Synthetic benchmark fixture.",
                    provider=name,
                    providers=(name,),
                )
                for domain, _ in sorted(
                    query.relevance.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            )
            for query in queries
        }

    async def search(
        self,
        parsed: Any,
        *,
        count: int,
        localization: str,
    ) -> ProviderSearchResult:
        candidates = self._responses.get(parsed.engine_query, ())
        return ProviderSearchResult(
            provider=self.name,
            candidates=tuple(candidates[:count]),
            providers_used=(self.name,),
        )

    async def healthcheck(self) -> bool:
        return True


async def run_provider(
    provider: BenchmarkProvider,
    queries: Sequence[GoldenQuery],
    *,
    count: int = 10,
) -> tuple[RunSummary, list[QueryMeasurement]]:
    measurements: list[QueryMeasurement] = []
    for query in queries:
        started = time.perf_counter()
        error: str | None = None
        urls: tuple[str, ...] = ()
        providers_used: tuple[str, ...] = ()
        try:
            result = await provider.search(
                parse_query(query.query),
                count=count,
                localization="",
            )
            urls = tuple(candidate.url for candidate in result.candidates[:count])
            providers_used = tuple(result.providers_used)
        except Exception as exc:  # a failed provider call is a measured failure
            error = f"{type(exc).__name__}: {exc}"
        duration_ms = (time.perf_counter() - started) * 1000
        score = ndcg_at_k(urls, query.relevance)
        measurements.append(
            QueryMeasurement(
                query_id=query.id,
                category=query.category,
                latency_ms=round(duration_ms, 3),
                ndcg_at_10=round(score, 6),
                success=error is None,
                error=error,
                result_urls=urls,
                providers_used=providers_used,
            )
        )
    return _summarize("provider", provider.name, measurements), measurements


async def run_federation(
    provider: FederatedSearchProvider,
    queries: Sequence[GoldenQuery],
    *,
    count: int = 10,
) -> tuple[RunSummary, list[QueryMeasurement]]:
    measurements: list[QueryMeasurement] = []
    for query in queries:
        started = time.perf_counter()
        error: str | None = None
        urls: tuple[str, ...] = ()
        providers_used: tuple[str, ...] = ()
        try:
            result = await provider.search(
                parse_query(query.query),
                count=count,
                localization="",
            )
            urls = tuple(candidate.url for candidate in result.candidates[:count])
            providers_used = tuple(result.providers_used)
        except Exception as exc:  # all providers failed
            error = f"{type(exc).__name__}: {exc}"
        duration_ms = (time.perf_counter() - started) * 1000
        score = ndcg_at_k(urls, query.relevance)
        measurements.append(
            QueryMeasurement(
                query_id=query.id,
                category=query.category,
                latency_ms=round(duration_ms, 3),
                ndcg_at_10=round(score, 6),
                success=error is None,
                error=error,
                result_urls=urls,
                providers_used=providers_used,
            )
        )
    return _summarize("federated", "federated", measurements), measurements


def _summarize(
    mode: str,
    provider: str,
    measurements: Sequence[QueryMeasurement],
) -> RunSummary:
    total = len(measurements)
    successful = sum(1 for item in measurements if item.success)
    failed = total - successful
    latencies = [item.latency_ms for item in measurements]
    ndcg = sum(item.ndcg_at_10 for item in measurements) / total if total else 0.0
    categories: dict[str, dict[str, float | int]] = {}
    for category in sorted({item.category for item in measurements}):
        selected = [item for item in measurements if item.category == category]
        category_successes = sum(1 for item in selected if item.success)
        categories[category] = {
            "queries": len(selected),
            "failed": len(selected) - category_successes,
            "failure_rate": (len(selected) - category_successes) / len(selected),
            "ndcg_at_10": sum(item.ndcg_at_10 for item in selected) / len(selected),
        }
    return RunSummary(
        mode=mode,
        provider=provider,
        total_queries=total,
        successful_queries=successful,
        failed_queries=failed,
        failure_rate=failed / total if total else 0.0,
        ndcg_at_10=round(ndcg, 6),
        latency_ms_p50=round(percentile(latencies, 0.50), 3),
        latency_ms_p95=round(percentile(latencies, 0.95), 3),
        categories=categories,
    )


def build_live_provider(
    name: str,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    retry_attempts: int,
    timeout_seconds: float | None,
    request_budget: int,
) -> SearchProvider:
    """Build the same resilient adapter used by the service for one provider."""

    configs = load_provider_configs(settings, provider_names=[name])
    if len(configs) != 1:
        raise RuntimeError(f"provider {name!r} is not configured")
    config = configs[0]
    raw_provider = build_provider(config, client)
    return ResilientProvider(
        raw_provider,
        ReliabilityConfig(
            timeout_seconds=timeout_seconds or config.timeout_seconds,
            retry_attempts=retry_attempts,
            retry_base_delay_seconds=settings.provider_retry_base_delay_seconds,
            circuit_failure_threshold=settings.provider_circuit_failure_threshold,
            circuit_reset_seconds=settings.provider_circuit_reset_seconds,
            requests_per_minute=max(config.requests_per_minute, request_budget),
            max_concurrency=config.max_concurrency,
        ),
    )


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render a benchmark report as a reproducible Markdown document."""

    config = report.get("config", {})
    golden_set = report.get("golden_set", {})
    lines = [
        "# Benchmarks",
        "",
        "This file is generated by `python -m benchmarks.run`. The harness evaluates "
        "provider result ordering and reciprocal-rank fusion against a manually judged, "
        "100-query golden set.",
        "",
        "## Run metadata",
        "",
        f"- Generated: `{report.get('generated_at', 'unknown')}`",
        f"- Mode: `{report.get('mode', 'unknown')}`",
        f"- Exact command: `{report.get('command', 'unknown')}`",
        f"- Golden set: `{golden_set.get('path', 'unknown')}`",
        f"- Golden set SHA-256: `{golden_set.get('sha256', 'unknown')}`",
        f"- Queries: `{golden_set.get('queries', 0)}` "
        f"({', '.join(golden_set.get('categories', {}).keys())})",
        f"- Results per query: `{config.get('count', 10)}`",
        f"- Provider timeout: `{config.get('timeout_seconds', 'provider default')}` seconds",
        f"- Attempts per provider call: `{config.get('retry_attempts', 1)}`",
        f"- Synthetic self-test: `{str(config.get('synthetic', False)).lower()}`",
        "",
        "## Results",
        "",
        "| Mode | Provider | Queries | nDCG@10 | p50 latency | p95 latency | Failure rate |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for result in report.get("results", []):
        if result.get("skipped_reason"):
            lines.append(
                f"| {result.get('mode')} | {result.get('provider')} | 0 | n/a | n/a | n/a | "
                f"skipped: {result['skipped_reason']} |"
            )
            continue
        lines.append(
            "| {mode} | {provider} | {total} | {ndcg:.4f} | {p50:.1f} ms | {p95:.1f} ms | "
            "{failure:.2%} |".format(
                mode=result.get("mode", ""),
                provider=result.get("provider", ""),
                total=result.get("total_queries", 0),
                ndcg=float(result.get("ndcg_at_10", 0.0)),
                p50=float(result.get("latency_ms_p50", 0.0)),
                p95=float(result.get("latency_ms_p95", 0.0)),
                failure=float(result.get("failure_rate", 0.0)),
            )
        )
    lines.extend(
        [
            "",
            "## Method",
            "",
            "- nDCG@10 uses exponential gains (`2^grade - 1`) and logarithmic discount.",
            "- Relevance judgments are domain-level. The most specific judged domain wins.",
            "- A failed logical query contributes nDCG 0 and is included in latency percentiles.",
            "- Direct-provider rows measure adapter retrieval. The federated row measures RRF "
            "ordering after canonical-URL deduplication.",
            "- Page fetching, reranking, billing, and HTTP API overhead are excluded.",
            "",
            "## Limitations and honest status",
            "",
        ]
    )
    if report.get("mode") == "self-test":
        lines.extend(
            [
                "- **This run is synthetic.** It proves the harness and metric implementation, "
                "but it is not evidence about Brave, Serper, Tavily, Exa, or SearXNG quality.",
                "- A live provider run requires that provider's official API credential. Rows "
                "skipped for missing credentials are not failures and have no quality score.",
                "- Live results vary with time, locale, upstream ranking changes, and provider "
                "coverage. Keep the generated JSON and this report with any release claim.",
                "- The golden set contains domain-level judgments, not exhaustive URL-level "
                "ratings, so nDCG is a reproducible relative signal rather than an absolute "
                "measure of web quality.",
                "- The benchmark does not measure end-to-end API latency, cached warm-cache "
                "latency, fetch latency, or the optional cross-encoder reranker.",
            ]
        )
    else:
        lines.extend(
            [
                "- Live results vary with time, locale, upstream ranking changes, and provider "
                "coverage.",
                "- The golden set contains domain-level judgments, not exhaustive URL-level "
                "ratings.",
                "- The benchmark does not measure end-to-end API latency, cached warm-cache "
                "latency, fetch latency, or the optional cross-encoder reranker.",
            ]
        )
    return "\n".join(lines) + "\n"


def build_report(
    *,
    mode: str,
    command: str,
    golden_path: Path,
    queries: Sequence[GoldenQuery],
    summaries: Sequence[RunSummary],
    measurements: Mapping[str, Sequence[QueryMeasurement]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    categories: dict[str, int] = {}
    for query in queries:
        categories[query.category] = categories.get(query.category, 0) + 1
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
        "mode": mode,
        "command": command,
        "golden_set": {
            "path": str(golden_path),
            "sha256": golden_set_digest(golden_path),
            "queries": len(queries),
            "categories": categories,
        },
        "config": dict(config),
        "results": [summary.to_dict() for summary in summaries],
        "measurements": {
            name: [asdict(measurement) for measurement in items]
            for name, items in measurements.items()
        },
    }


def command_for_report(arguments: Sequence[str]) -> str:
    return shlex.join([".venv/bin/python", "-m", "benchmarks.run", *arguments])


def write_report(report: Mapping[str, Any], output_path: Path | None, markdown_path: Path | None) -> None:
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
