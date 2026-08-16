"""End-to-end slice test, fully offline: ingest → bronze → transform → SQLite."""

import json
from pathlib import Path

import httpx

from gridpulse.cli import cmd_ingest, cmd_transform
from gridpulse.client import EiaClient
from gridpulse.config import Settings
from gridpulse.storage import connect, table_checksum

FIXTURES = Path(__file__).parent / "fixtures"


def offline_client(settings: Settings) -> EiaClient:
    payload = json.loads((FIXTURES / "demand_hourly_4regions.json").read_text(encoding="utf-8"))
    payload["response"]["total"] = str(len(payload["response"]["data"]))  # wire shape: string

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api_key" in dict(request.url.params)
        return httpx.Response(200, json=payload)

    return EiaClient(settings, transport=httpx.MockTransport(handler))


def test_vertical_slice_end_to_end(tmp_path):
    settings = Settings(api_key="test-key", data_dir=tmp_path / "data")

    assert cmd_ingest(settings, "2026-08-13T00", "2026-08-14T00",
                      client=offline_client(settings)) == 0
    bronze = list(settings.bronze_dir.glob("*.json"))
    assert len(bronze) == 1
    assert "test-key" not in bronze[0].read_text(encoding="utf-8")

    assert cmd_transform(settings) == 0
    conn = connect(settings.db_path)
    n = conn.execute("SELECT COUNT(*) FROM demand_hourly").fetchone()[0]
    assert n == 20  # the fixture page holds 20 rows (server total is 100)
    stages = [r[0] for r in conn.execute("SELECT stage FROM pipeline_runs ORDER BY stage")]
    assert stages == ["ingest", "transform"]

    # Transform again over the same bronze: the silver table must not move.
    before = table_checksum(conn, "demand_hourly")
    assert cmd_transform(settings) == 0
    assert table_checksum(conn, "demand_hourly") == before


def test_metrics_ledger_records_counts_and_sha(tmp_path):
    settings = Settings(api_key="test-key", data_dir=tmp_path / "data")
    cmd_ingest(settings, "a", "b", client=offline_client(settings))
    cmd_transform(settings)
    conn = connect(settings.db_path)
    run = conn.execute(
        "SELECT rows_valid, rows_quarantined, git_sha FROM pipeline_runs "
        "WHERE stage='transform'"
    ).fetchone()
    assert run[0] == 20 and run[1] == 0
    assert run[2]  # git sha or "unknown", never empty
