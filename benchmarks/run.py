"""Command-line entry point for SearchRelay benchmarks.

Examples::

    # Validate the harness without network access or provider credentials.
    .venv/bin/python -m benchmarks.run --self-test

    # Measure configured live providers and write a reproducible report.
    .venv/bin/python -m benchmarks.run \
      --providers brave,serper,tavily,exa,searxng \
      --output benchmarks/results/live.json \
      --markdown docs/benchmarks.md
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Sequence

import httpx

from app.config import Settings
from app.providers.federation import FederatedSearchProvider
from benchmarks.harness import (
    RunSummary,
    SyntheticProvider,
    build_live_provider,
    build_report,
    command_for_report,
    load_golden_set,
    run_federation,
    run_provider,
    write_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.run",
        description="Measure provider and federated search quality and latency.",
    )
    parser.add_argument(
        "--golden-set",
        default="benchmarks/golden_set.jsonl",
        help="JSONL golden set path",
    )
    parser.add_argument(
        "--providers",
        default=os.getenv("SF_PROVIDER_PRIORITIES", "searxng"),
        help="Comma-separated providers to measure",
    )
    parser.add_argument("--count", type=int, default=10, help="Results requested per query")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Override each provider timeout in seconds",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=None,
        help="Attempts per provider call, including the first",
    )
    parser.add_argument(
        "--request-budget",
        type=int,
        default=100_000,
        help="Minimum per-provider requests-per-minute budget for the run",
    )
    parser.add_argument(
        "--searxng-url",
        default=None,
        help="Override SF_UPSTREAM_URL for the SearXNG provider",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Use synthetic providers derived from the golden judgments",
    )
    parser.add_argument("--output", help="Write the complete JSON report to this path")
    parser.add_argument("--markdown", help="Write the Markdown results table to this path")
    return parser


async def run(args: argparse.Namespace, arguments: Sequence[str]) -> int:
    golden_path = Path(args.golden_set)
    queries = load_golden_set(golden_path)
    provider_names = [
        name.strip().casefold()
        for name in args.providers.split(",")
        if name.strip()
    ]
    if not provider_names:
        raise SystemExit("--providers must contain at least one provider")
    if args.count < 1 or args.count > 50:
        raise SystemExit("--count must be between 1 and 50")
    if args.timeout is not None and args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    if args.retries is not None and args.retries < 1:
        raise SystemExit("--retries must be at least 1")

    settings = Settings(
        upstream_url=args.searxng_url or os.getenv("SF_UPSTREAM_URL", "http://searxng:8080"),
        provider_priorities=",".join(provider_names),
    )
    retry_attempts = args.retries or settings.provider_retry_attempts
    timeout_seconds = args.timeout
    summaries: list[RunSummary] = []
    all_measurements: dict[str, list[Any]] = {}

    if args.self_test:
        mode = "self-test"
        for name in provider_names:
            synthetic_provider = SyntheticProvider(f"synthetic-{name}", queries)
            summary, measurements = await run_provider(
                synthetic_provider,
                queries,
                count=args.count,
            )
            summaries.append(summary)
            all_measurements[f"provider:{name}"] = measurements
        federation = FederatedSearchProvider(
            tuple(SyntheticProvider(f"synthetic-{name}", queries) for name in provider_names)
        )
        summary, measurements = await run_federation(federation, queries, count=args.count)
        summaries.append(summary)
        all_measurements["federated"] = measurements
    else:
        mode = "live"
        async with httpx.AsyncClient(trust_env=False) as client:
            direct_providers = []
            federation_providers = []
            for name in provider_names:
                try:
                    direct = build_live_provider(
                        name,
                        settings=settings,
                        client=client,
                        retry_attempts=retry_attempts,
                        timeout_seconds=timeout_seconds,
                        request_budget=args.request_budget,
                    )
                    federation_provider = build_live_provider(
                        name,
                        settings=settings,
                        client=client,
                        retry_attempts=retry_attempts,
                        timeout_seconds=timeout_seconds,
                        request_budget=args.request_budget,
                    )
                except RuntimeError as exc:
                    summaries.append(
                        RunSummary(
                            mode="provider",
                            provider=name,
                            total_queries=0,
                            successful_queries=0,
                            failed_queries=0,
                            failure_rate=0.0,
                            ndcg_at_10=0.0,
                            latency_ms_p50=0.0,
                            latency_ms_p95=0.0,
                            skipped_reason=str(exc),
                        )
                    )
                    continue
                direct_providers.append(direct)
                federation_providers.append(federation_provider)

            for live_provider in direct_providers:
                summary, measurements = await run_provider(
                    live_provider,
                    queries,
                    count=args.count,
                )
                summaries.append(summary)
                all_measurements[f"provider:{live_provider.name}"] = measurements

            if federation_providers:
                federation = FederatedSearchProvider(tuple(federation_providers))
                summary, measurements = await run_federation(
                    federation,
                    queries,
                    count=args.count,
                )
                summaries.append(summary)
                all_measurements["federated"] = measurements
            else:
                summaries.append(
                    RunSummary(
                        mode="federated",
                        provider="federated",
                        total_queries=0,
                        successful_queries=0,
                        failed_queries=0,
                        failure_rate=0.0,
                        ndcg_at_10=0.0,
                        latency_ms_p50=0.0,
                        latency_ms_p95=0.0,
                        skipped_reason="no live provider was configured",
                    )
                )

    config = {
        "count": args.count,
        "providers": provider_names,
        "timeout_seconds": timeout_seconds or "provider default",
        "retry_attempts": retry_attempts,
        "request_budget_per_minute": args.request_budget,
        "synthetic": args.self_test,
        "result_source": "provider adapters (page fetching excluded)",
    }
    report = build_report(
        mode=mode,
        command=command_for_report(arguments),
        golden_path=golden_path,
        queries=queries,
        summaries=summaries,
        measurements=all_measurements,
        config=config,
    )
    write_report(
        report,
        Path(args.output) if args.output else None,
        Path(args.markdown) if args.markdown else None,
    )

    print(f"mode={mode} queries={len(queries)} providers={','.join(provider_names)}")
    for summary in summaries:
        if summary.skipped_reason:
            print(
                f"mode={summary.mode} provider={summary.provider} skipped={summary.skipped_reason}"
            )
            continue
        print(
            f"mode={summary.mode} provider={summary.provider} "
            f"ndcg@10={summary.ndcg_at_10:.4f} "
            f"p50_ms={summary.latency_ms_p50:.1f} p95_ms={summary.latency_ms_p95:.1f} "
            f"failure_rate={summary.failure_rate:.2%}"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(arguments)
    return asyncio.run(run(args, arguments))


if __name__ == "__main__":
    raise SystemExit(main())
