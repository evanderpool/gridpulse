"""Client tests: pagination, retry/backoff, the pull budget, and key scrubbing.

All offline via httpx.MockTransport; sleeps are captured, never real.
"""

import gzip
import json
from pathlib import Path

import httpx
import pytest

from conftest import good_row, wire_page
from gridpulse.client import EiaClient, PullBudgetExceeded, write_bronze
from gridpulse.config import PAGE_LENGTH, Settings


def settings(tmp_path, **kw) -> Settings:
    return Settings(api_key="test-key", data_dir=tmp_path, **kw)


def test_paginates_until_server_total_reached(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(dict(request.url.params)["offset"])
        calls.append(offset)
        rows = [good_row(value=i) for i in range(offset, min(offset + PAGE_LENGTH, 7000))]
        return httpx.Response(200, json=wire_page(rows, total=7000))

    with EiaClient(settings(tmp_path), transport=httpx.MockTransport(handler)) as client:
        pages = client.fetch_demand_window("2026-08-01T00", "2026-08-14T00")
    assert calls == [0, PAGE_LENGTH]
    assert sum(len(p["response"]["data"]) for p in pages) == 7000


def test_retries_on_500_with_backoff_then_succeeds(tmp_path):
    attempts, sleeps = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(500)
        return httpx.Response(200, json=wire_page([good_row()]))

    client = EiaClient(
        settings(tmp_path), transport=httpx.MockTransport(handler), sleep=sleeps.append
    )
    pages = client.fetch_demand_window("a", "b")
    assert len(attempts) == 3
    assert sleeps == [1.0, 2.0]  # exponential
    assert pages[0]["response"]["total"] == "1"


def test_retry_exhaustion_on_5xx_names_the_status(tmp_path):
    # Retry EXHAUSTION (budget still open) must fail with the status and a
    # fix path — previously uncovered (code-review finding MED-8).
    client = EiaClient(
        settings(tmp_path, max_retries=2, max_requests_per_run=99),
        transport=httpx.MockTransport(lambda r: httpx.Response(503)),
        sleep=lambda s: None,
    )
    with pytest.raises(RuntimeError, match="503"):
        client.fetch_demand_window("a", "b")


def test_transport_errors_retry_then_fail_with_fix_path(tmp_path):
    # Connection-level failures take the same backoff path as HTTP 5xx.
    attempts, sleeps = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("boom")

    client = EiaClient(
        settings(tmp_path, max_retries=2, max_requests_per_run=99),
        transport=httpx.MockTransport(handler), sleep=sleeps.append,
    )
    with pytest.raises(RuntimeError, match="check connectivity"):
        client.fetch_demand_window("a", "b")
    assert len(attempts) == 3  # initial + 2 retries
    assert sleeps == [1.0, 2.0]


def test_pull_budget_is_a_hard_cap_with_fix_path(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=wire_page([good_row()] * PAGE_LENGTH, total=50_000))

    client = EiaClient(
        settings(tmp_path, max_requests_per_run=2), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(PullBudgetExceeded, match="narrow the window"):
        client.fetch_demand_window("a", "b")
    assert client.requests_made == 2


def test_retries_count_against_the_budget(tmp_path):
    # A flapping server must not let retries bypass the spend cap.
    client = EiaClient(
        settings(tmp_path, max_requests_per_run=3, max_retries=99),
        transport=httpx.MockTransport(lambda r: httpx.Response(500)), sleep=lambda s: None,
    )
    with pytest.raises(PullBudgetExceeded):
        client.fetch_demand_window("a", "b")
    assert client.requests_made == 3


def test_unrecognized_envelope_fails_with_fix_path(tmp_path):
    # Pydantic-at-the-boundary covers the envelope too: a renamed key must
    # not surface as a bare KeyError.
    client = EiaClient(
        settings(tmp_path),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"response": {"totalRows": "1"}})
        ),
    )
    with pytest.raises(RuntimeError, match="schemas/raw.py"):
        client.fetch_demand_window("a", "b")


def read_bronze_text(path: Path) -> str:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return f.read()


def test_bronze_scrubs_the_api_key_echo(tmp_path):
    # EIA echoes request params (key included) back in the payload — the
    # Phase 0 lesson. Bronze must never contain it.
    paths = write_bronze([wire_page([good_row()])], "run1", tmp_path / "bronze", "w")
    text = read_bronze_text(paths[0])
    assert "test-key" not in text
    doc = json.loads(text)
    assert doc["payload"]["response"]["data"]  # payload otherwise intact
    assert "fetched_at" in doc


def test_bronze_filenames_carry_window_and_run(tmp_path):
    paths = write_bronze(
        [wire_page([good_row()]), wire_page([good_row()])], "runX", tmp_path, "W"
    )
    names = [p.name for p in paths]
    assert names == ["demand_W_p000_runX.json.gz", "demand_W_p001_runX.json.gz"]
    assert all((Path(tmp_path) / n).exists() for n in names)
