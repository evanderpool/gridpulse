"""Silver → gold end to end, including recompute determinism."""

from conftest import bronze_rows, good_row
from gridpulse.convert.backends import get_backend
from gridpulse.convert.derive import derive_to_gold
from gridpulse.convert.pipeline import convert_demand, convert_fuelmix
from gridpulse.storage import connect, table_checksum, upsert_demand, upsert_fuelmix


def fuel_row(**overrides) -> dict:
    row = {"period": "2026-08-13T18", "respondent": "ERCO", "fueltype": "NG",
           "value": 7000, "value-units": "megawatthours"}
    row.update(overrides)
    return row


def seeded_conn(tmp_path):
    conn = connect(tmp_path / "t.db")
    demand, _ = convert_demand([bronze_rows([good_row(value=10_000)])])
    fuel, _ = convert_fuelmix([bronze_rows([
        fuel_row(fueltype="SUN", value=2000),
        fuel_row(fueltype="WND", value=1000),
        fuel_row(fueltype="NG", value=7000),
    ])])
    upsert_demand(conn, demand)
    upsert_fuelmix(conn, fuel)
    return conn


def test_derive_writes_gold_from_silver(tmp_path):
    conn = seeded_conn(tmp_path)
    written = derive_to_gold(conn, get_backend("polars"))
    assert written == 1
    share, net_load = conn.execute(
        "SELECT renewable_share, net_load_mwh FROM metrics_hourly"
    ).fetchone()
    assert share == 0.3
    assert net_load == 7_000
    conn.close()


def test_derive_recompute_is_deterministic(tmp_path):
    conn = seeded_conn(tmp_path)
    backend = get_backend("polars")
    derive_to_gold(conn, backend)
    first = table_checksum(conn, "metrics_hourly")
    derive_to_gold(conn, backend)
    assert table_checksum(conn, "metrics_hourly") == first
    conn.close()
