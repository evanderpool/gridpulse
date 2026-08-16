"""End-to-end slice test, fully offline: ingest → bronze → transform → SQLite."""

import re

import httpx

from gridpulse.cli import cmd_ingest, cmd_transform
from gridpulse.client import EiaClient
from gridpulse.config import Settings
from gridpulse.storage import connect, table_checksum


def offline_client(settings: Settings, payload: dict) -> EiaClient:
    payload = {**payload}
    payload["response"] = {**payload["response"],
                           "total": str(len(payload["response"]["data"]))}

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api_key" in dict(request.url.params)
        return httpx.Response(200, json=payload)

    return EiaClient(settings, transport=httpx.MockTransport(handler))


def test_vertical_slice_end_to_end(tmp_path, demand_payload):
    settings = Settings(api_key="test-key", data_dir=tmp_path / "data")

    assert cmd_ingest(settings, "2026-08-13T00", "2026-08-14T00",
                      client=offline_client(settings, demand_payload)) == 0
    bronze = list(settings.bronze_dir.glob("*.json"))
    assert len(bronze) == 1
    assert "test-key" not in bronze[0].read_text(encoding="utf-8")

    assert cmd_transform(settings) == 0
    conn = connect(settings.db_path)
    n = conn.execute("SELECT COUNT(*) FROM demand_hourly").fetchone()[0]
    assert n == 20  # the fixture page holds 20 rows (server total is 100)
    stages = [r[0] for r in conn.execute("SELECT stage FROM pipeline_runs ORDER BY stage")]
    assert stages == ["ingest", "transform"]

    # Transform again over the same bronze: the silver table must not move,
    # and the ledger must SEE the no-op (0 upserted).
    before = table_checksum(conn, "demand_hourly")
    assert cmd_transform(settings) == 0
    assert table_checksum(conn, "demand_hourly") == before
    last_upserted = conn.execute(
        "SELECT rows_upserted FROM pipeline_runs WHERE stage='transform' "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()[0]
    assert last_upserted == 0
    conn.close()


def test_metrics_ledger_records_counts_and_sha(tmp_path, demand_payload):
    settings = Settings(api_key="test-key", data_dir=tmp_path / "data")
    cmd_ingest(settings, "a", "b", client=offline_client(settings, demand_payload))
    cmd_transform(settings)
    conn = connect(settings.db_path)
    valid, quarantined, sha = conn.execute(
        "SELECT rows_valid, rows_quarantined, git_sha FROM pipeline_runs "
        "WHERE stage='transform'"
    ).fetchone()
    assert valid == 20 and quarantined == 0
    assert re.fullmatch(r"[0-9a-f]{7,}|unknown", sha)
    conn.close()
