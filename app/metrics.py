"""Prometheus metrics for the SearchRelay service.

Each application instance owns its own registry so that repeated
``create_app`` calls (tests, workers) never fail on duplicate collectors.
"""

from __future__ import annotations

from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)


class Metrics:
    """Container for the service metrics."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self.http_requests = Counter(
            "searchrelay_http_requests_total",
            "HTTP requests handled by the service.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "searchrelay_http_request_duration_seconds",
            "HTTP request duration in seconds.",
            ["method", "route"],
            registry=self.registry,
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
        )
        self.provider_requests = Counter(
            "searchrelay_provider_requests_total",
            "Upstream provider search calls.",
            ["provider", "outcome"],
            registry=self.registry,
        )
        self.provider_duration = Histogram(
            "searchrelay_provider_request_duration_seconds",
            "Upstream provider search duration in seconds.",
            ["provider"],
            registry=self.registry,
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
        )
        self.cache_operations = Counter(
            "searchrelay_cache_operations_total",
            "Cache lookups and stores.",
            ["tier", "operation", "result"],
            registry=self.registry,
        )
        self.billing_charges = Counter(
            "searchrelay_billing_charges_total",
            "Billing attempts by action and result.",
            ["action", "result"],
            registry=self.registry,
        )
        self.rate_limit_rejections = Counter(
            "searchrelay_rate_limit_rejections_total",
            "Requests rejected by a rate limiter.",
            ["scope"],
            registry=self.registry,
        )
        self.build_info = Gauge(
            "searchrelay_build_info",
            "Build information.",
            ["version", "python_version"],
            registry=self.registry,
        )

    def observe_request(self, *, method: str, route: str, status: int, duration: float) -> None:
        self.http_requests.labels(method=method, route=route, status=str(status)).inc()
        self.http_duration.labels(method=method, route=route).observe(duration)

    def bind_cache(self, cache: Any) -> None:
        """Expose the live cache counters as gauges."""

        stats = cache.stats
        for name, attribute in (
            ("searchrelay_cache_hits", "hits"),
            ("searchrelay_cache_misses", "misses"),
            ("searchrelay_cache_memory_hits", "memory_hits"),
            ("searchrelay_cache_disk_hits", "disk_hits"),
            ("searchrelay_cache_stores", "stores"),
        ):
            gauge = Gauge(
                name,
                "Live cache counters.",
                registry=self.registry,
            )
            gauge.set_function(lambda attribute=attribute: float(getattr(stats, attribute)))

    def record_billing(self, *, action: str, charged: bool) -> None:
        self.billing_charges.labels(action=action, result="charged" if charged else "rejected").inc()

    def record_rate_limit_rejection(self, scope: str) -> None:
        self.rate_limit_rejections.labels(scope=scope).inc()

    def observe_provider(self, *, provider: str, outcome: str, duration: float) -> None:
        self.provider_requests.labels(provider=provider, outcome=outcome).inc()
        self.provider_duration.labels(provider=provider).observe(duration)

    def render(self) -> tuple[bytes, str]:
        """Return the exposition payload and its content type."""

        return generate_latest(self.registry), CONTENT_TYPE_LATEST


def route_label(request: Any, fallback: str = "unmatched") -> str:
    """Return the route template of a request, falling back to the raw path."""

    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return fallback
