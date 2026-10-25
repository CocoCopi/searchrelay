from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app.providers.federation import FederatedSearchProvider
from benchmarks.harness import (
    GoldenQuery,
    SyntheticProvider,
    domain_grade,
    load_golden_set,
    ndcg_at_k,
    percentile,
    run_federation,
    run_provider,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_SET = ROOT / "benchmarks" / "golden_set.jsonl"


def test_golden_set_has_100_queries_across_four_categories() -> None:
    queries = load_golden_set(GOLDEN_SET)

    assert len(queries) == 100
    categories: dict[str, int] = {}
    for query in queries:
        categories[query.category] = categories.get(query.category, 0) + 1
    assert categories == {"factual": 25, "news": 25, "code": 25, "multilingual": 25}
    assert len({query.id for query in queries}) == 100


def test_golden_set_rejects_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"id": "bad", "category": "x", "query": "x", ', encoding="utf-8")

    with pytest.raises(ValueError, match="invalid JSON"):
        load_golden_set(path)


def test_domain_grade_prefers_the_most_specific_judgment() -> None:
    relevance = {"europa.eu": 1, "ec.europa.eu": 3}

    assert domain_grade("https://ec.europa.eu/page", relevance) == 3
    assert domain_grade("https://europa.eu/page", relevance) == 1
    assert domain_grade("https://example.org/page", relevance) == 0


def test_ndcg_uses_exponential_gain_and_log_discount() -> None:
    assert ndcg_at_k(["https://example.org/a"], {"example.org": 3}) == pytest.approx(1.0)

    relevance = {"example.org": 3, "other.test": 1}
    expected = 1 / (7 + 1 / math.log2(3))
    assert ndcg_at_k(["https://other.test/a"], relevance) == pytest.approx(expected)
    assert ndcg_at_k(["https://unknown.test/a"], relevance) == 0.0


def test_percentile_uses_linear_interpolation() -> None:
    assert percentile([10, 20, 30, 40], 0.5) == 25
    assert percentile([10, 20, 30, 40], 0.95) == pytest.approx(38.5)
    assert percentile([], 0.5) == 0.0


@pytest.mark.asyncio
async def test_synthetic_provider_and_federation_self_test_pass() -> None:
    queries = [
        GoldenQuery(
            id="q1",
            category="test",
            query="example query",
            relevance={"example.org": 3, "fallback.test": 1},
        )
    ]

    provider_summary, _ = await run_provider(
        SyntheticProvider("synthetic-example", queries),
        queries,
    )
    federation_summary, _ = await run_federation(
        FederatedSearchProvider(
            (
                SyntheticProvider("synthetic-one", queries),
                SyntheticProvider("synthetic-two", queries),
            )
        ),
        queries,
    )

    assert provider_summary.ndcg_at_10 == 1.0
    assert provider_summary.failure_rate == 0.0
    assert federation_summary.ndcg_at_10 == 1.0
    assert federation_summary.failure_rate == 0.0


def test_golden_set_file_is_strict_jsonl() -> None:
    for line in GOLDEN_SET.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        assert isinstance(payload["relevance"], dict)
