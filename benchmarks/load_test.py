"""Locust load test for SearchRelay's public API.

Run against a locally running service::

    export SEARCHRELAY_API_KEY=sk-test
    .venv/bin/locust -f benchmarks/load_test.py --host http://127.0.0.1:8000 \
      --users 20 --spawn-rate 5 --run-time 2m --headless

The load test intentionally calls only ``/v1/tools/search`` with
``need_content=false``. It does not assert a fixed latency target; use the
Locust CSV/HTML output to compare runs on the same hardware and configuration.
"""

from __future__ import annotations

import os

from locust import HttpUser, between, task


class SearchRelayUser(HttpUser):
    wait_time = between(0.05, 0.25)

    def on_start(self) -> None:
        api_key = os.getenv("SEARCHRELAY_API_KEY", "sk-test")
        self.client.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
        )

    @task
    def search(self) -> None:
        self.client.post(
            "/v1/tools/search",
            json={
                "query": "python asyncio documentation",
                "count": 10,
                "need_content": False,
            },
            name="POST /v1/tools/search",
        )
