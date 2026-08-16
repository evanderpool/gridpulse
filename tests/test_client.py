"""Client tests: pagination, retry/backoff, the pull budget, and key scrubbing.

All offline via httpx.MockTransport; sleeps are captured, never real.
"""

import json
from pathlib import Path

import httpx
import pytest

from gridpulse.client import EiaClient, PullBudgetExceeded, write_bronze
from gridpulse.config import PAGE_LENGTH, Settings


def settings(tmp_path, **kw) -> Settings:
    return Settings(api_key="test-key", data_dir=tmp_path, **kw)


def page(rows: list[dict], total: int) -> dict:
    return {"response": {"total": total, "data": rows},
            "request": {"command": "/v2/...", "params": {"api_key": "test-key"}}}


def row(i: int) -> dict:
    return {"period": "2026-08-13T18", "respondent": "ERCO", "type": "D",
            "value": i, "value-units": "megawatthours"}


def test_paginates_until_server_total_reached(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(dict(request.url.params)["offset"])
        calls.append(offset)
        rows = [row(i) for i in range(offset, min(offset + PAGE_LENGTH, 7000))]
        return httpx.Response(200, json=page(rows, total=7000))

    client = EiaClient(settings(tmp_path), transport=httpx.MockTransport(handler))
    pages = client.fetch_demand_window("2026-08-01T00", "2026-08-14T00")
    assert calls == [0, PAGE_LENGTH]
    assert sum(len(p["response"]["data"]) for p in pages) == 7000


def test_retries_on_500_with_backoff_then_succeeds(tmp_path):
    attempts, sleeps = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(500)
        return httpx.Response(200, json=page([row(1)], total=1))

    client = EiaClient(
        settings(tmp_path), transport=httpx.MockTransport(handler), sleep=sleeps.append
    )
    pages = client.fetch_demand_window("a", "b")
    assert len(attempts) == 3
    assert sleeps == [1.0, 2.0]  # exponential
    assert pages[0]["response"]["total"] == 1


def test_pull_budget_is_a_hard_cap_with_fix_path(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page([row(1)] * PAGE_LENGTH, total=50_000))

    client = EiaClient(
        settings(tmp_path, max_requests_per_run=2), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(PullBudgetExceeded, match="narrow the window"):
        client.fetch_demand_window("a", "b")
    assert client.requests_made == 2


def test_retries_count_against_the_budget(tmp_path):
    # A flapping server must not let retries bypass the spend cap.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = EiaClient(
        settings(tmp_path, max_requests_per_run=3, max_retries=99),
        transport=httpx.MockTransport(handler), sleep=lambda s: None,
    )
    with pytest.raises(PullBudgetExceeded):
        client.fetch_demand_window("a", "b")
    assert client.requests_made == 3


def test_bronze_scrubs_the_api_key_echo(tmp_path):
    # EIA echoes request params (key included) back in the payload — the
    # Phase 0 lesson. Bronze must never contain it.
    paths = write_bronze([page([row(1)], total=1)], "run1", tmp_path / "bronze", "w")
    text = paths[0].read_text(encoding="utf-8")
    assert "test-key" not in text
    doc = json.loads(text)
    assert doc["payload"]["response"]["data"]  # payload otherwise intact
    assert "fetched_at" in doc


def test_bronze_filenames_carry_window_and_run(tmp_path):
    paths = write_bronze([page([row(1)], 1), page([row(2)], 1)], "runX", tmp_path, "W")
    names = [p.name for p in paths]
    assert names == ["demand_W_p000_runX.json", "demand_W_p001_runX.json"]
    assert all((Path(tmp_path) / n).exists() for n in names)
