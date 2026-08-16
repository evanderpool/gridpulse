"""End-to-end test, fully offline: ingest → bronze → transform → derive → gold."""

import re

import httpx

from conftest import fuel_row, wire_page
from gridpulse.cli import cmd_derive, cmd_ingest, cmd_transform
from gridpulse.client import EiaClient
from gridpulse.config import DEMAND_ROUTE, Settings
from gridpulse.storage import connect, table_checksum

# Hour 23 exists in the demand fixture for ERCO, so gold gets exactly one
# joined (region, period) row.
FUELMIX_ROWS = [
    fuel_row(period="2026-08-13T23", fueltype="SUN", value=2000),
    fuel_row(period="2026-08-13T23", fueltype="NG", value=8000),
]


def offline_client(settings: Settings, demand_payload: dict) -> EiaClient:
    demand_payload = {**demand_payload}
    demand_payload["response"] = {
        **demand_payload["response"],
        "total": str(len(demand_payload["response"]["data"])),  # wire shape: string
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api_key" in dict(request.url.params)
        if DEMAND_ROUTE.rstrip("/") in str(request.url):
            return httpx.Response(200, json=demand_payload)
        return httpx.Response(200, json=wire_page(FUELMIX_ROWS))

    return EiaClient(settings, transport=httpx.MockTransport(handler))


def test_full_pipeline_end_to_end(tmp_path, demand_payload):
    settings = Settings(api_key="test-key", data_dir=tmp_path / "data")

    assert cmd_ingest(settings, "2026-08-13T00", "2026-08-14T00",
                      client=offline_client(settings, demand_payload)) == 0
    bronze = sorted(p.name for p in settings.bronze_dir.glob("*.json"))
    assert len(bronze) == 2
    assert bronze[0].startswith("demand_") and bronze[1].startswith("fuelmix_")
    assert all("test-key" not in (settings.bronze_dir / n).read_text(encoding="utf-8")
               for n in bronze)

    assert cmd_transform(settings) == 0
    assert cmd_derive(settings) == 0
    conn = connect(settings.db_path)
    assert conn.execute("SELECT COUNT(*) FROM demand_hourly").fetchone()[0] == 20
    assert conn.execute("SELECT COUNT(*) FROM fuelmix_hourly").fetchone()[0] == 2
    # Gold: the one hour with both demand and fuel data → share 0.2.
    share = conn.execute(
        "SELECT renewable_share FROM metrics_hourly WHERE region='ERCO'"
    ).fetchone()[0]
    assert share == 0.2
    stages = [r[0] for r in conn.execute("SELECT stage FROM pipeline_runs ORDER BY stage")]
    assert stages == ["derive", "ingest", "transform_demand", "transform_fuelmix"]

    # Transform + derive again: silver and gold must not move, and the
    # ledger must SEE the no-op (0 upserted).
    before = (table_checksum(conn, "demand_hourly"), table_checksum(conn, "fuelmix_hourly"),
              table_checksum(conn, "metrics_hourly"))
    assert cmd_transform(settings) == 0
    assert cmd_derive(settings) == 0
    after = (table_checksum(conn, "demand_hourly"), table_checksum(conn, "fuelmix_hourly"),
             table_checksum(conn, "metrics_hourly"))
    assert after == before
    for stage in ("transform_demand", "transform_fuelmix"):
        last_upserted = conn.execute(
            "SELECT rows_upserted FROM pipeline_runs WHERE stage=? "
            "ORDER BY started_at DESC LIMIT 1", (stage,)
        ).fetchone()[0]
        assert last_upserted == 0
    conn.close()


def test_metrics_ledger_records_counts_and_sha(tmp_path, demand_payload):
    settings = Settings(api_key="test-key", data_dir=tmp_path / "data")
    cmd_ingest(settings, "a", "b", client=offline_client(settings, demand_payload))
    cmd_transform(settings)
    conn = connect(settings.db_path)
    # Per-dataset ledger rows: a fuelmix-only change is attributable.
    received, valid, quarantined, sha = conn.execute(
        "SELECT rows_received, rows_valid, rows_quarantined, git_sha "
        "FROM pipeline_runs WHERE stage='transform_demand'"
    ).fetchone()
    assert received == 20 and valid == 20 and quarantined == 0
    assert re.fullmatch(r"[0-9a-f]{7,}|unknown", sha)
    fuel_valid = conn.execute(
        "SELECT rows_valid FROM pipeline_runs WHERE stage='transform_fuelmix'"
    ).fetchone()[0]
    assert fuel_valid == 2
    conn.close()


def test_single_dataset_ingest_flag(tmp_path, demand_payload):
    settings = Settings(api_key="test-key", data_dir=tmp_path / "data")
    cmd_ingest(settings, "a", "b", client=offline_client(settings, demand_payload),
               datasets=("demand",))
    names = [p.name for p in settings.bronze_dir.glob("*.json")]
    assert len(names) == 1 and names[0].startswith("demand_")
    conn = connect(settings.db_path)
    assert conn.execute("SELECT rows_received FROM pipeline_runs").fetchone()[0] == 20
    conn.close()
