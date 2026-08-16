"""analyze + report: the three questions answered, the page self-contained."""

from datetime import datetime, timezone

from conftest import bronze_rows, fuel_row, good_row
from gridpulse.analyze import coverage, question_answers
from gridpulse.cli import _month_start, auto_window, cmd_report
from gridpulse.config import Settings
from gridpulse.convert.backends import get_backend
from gridpulse.convert.derive import derive_to_gold
from gridpulse.convert.pipeline import convert_demand, convert_fuelmix
from gridpulse.report import build_report
from gridpulse.storage import connect, upsert_demand, upsert_fuelmix


def seed(conn):
    demand_rows = [good_row(period=f"2026-08-13T{h:02d}", value=10_000 + h * 100)
                   for h in range(6)]
    fuel = []
    for h in range(6):
        fuel += [fuel_row(period=f"2026-08-13T{h:02d}", fueltype="SUN", value=2_000),
                 fuel_row(period=f"2026-08-13T{h:02d}", fueltype="NG", value=8_000)]
    d, _ = convert_demand([bronze_rows(demand_rows)])
    f, _ = convert_fuelmix([bronze_rows(fuel)])
    upsert_demand(conn, d)
    upsert_fuelmix(conn, f)
    derive_to_gold(conn, get_backend())


def test_question_answers_from_seeded_db(tmp_path):
    conn = connect(tmp_path / "t.db")
    seed(conn)
    a = question_answers(conn)
    assert a["q1"]["ERCO"] == 20.0                       # 2000/10000 everywhere
    assert a["q2"]["ERCO"]["peak_mwh"] == 10_500         # hour 05
    assert a["q3"] is None                                # no CISO data seeded
    cov = coverage(conn)
    assert cov["hours"] == 6
    conn.close()


def test_report_is_selfcontained_and_answers_present(tmp_path):
    conn = connect(tmp_path / "t.db")
    seed(conn)
    html = build_report(conn)
    for marker in ("<title>GridPulse</title>", "duck curve", "Renewable share",
                   "Run ledger", "quarantined", "ERCOT"):
        assert marker in html
    # Self-contained: no external fetches, no API traces.
    assert "http" not in html.replace("https://github.com/evanderpool/gridpulse", "") \
        .replace("https://www.eia.gov", "")  # only the two footer links
    assert "api_key" not in html
    assert "<script src" not in html and "<link" not in html
    conn.close()


def test_cmd_report_writes_file_and_ledger_row(tmp_path):
    settings = Settings(api_key="k", data_dir=tmp_path / "data")
    conn = connect(settings.db_path)
    seed(conn)
    conn.close()
    out = tmp_path / "site" / "index.html"
    assert cmd_report(settings, out) == 0
    assert out.exists() and "GridPulse" in out.read_text(encoding="utf-8")
    conn = connect(settings.db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM pipeline_runs WHERE stage='report'"
    ).fetchone()[0] == 1
    conn.close()


def test_auto_window_incremental_and_first_run(tmp_path):
    settings = Settings(api_key="k", data_dir=tmp_path / "data")
    # Empty DB → first-run window of 7 days.
    start, end = auto_window(settings)
    fmt = "%Y-%m-%dT%H"
    span = datetime.strptime(end, fmt) - datetime.strptime(start, fmt)
    assert 6.9 <= span.total_seconds() / 86400 <= 7.1
    # Populated DB → last hour minus the 48h revision overlap.
    conn = connect(settings.db_path)
    d, _ = convert_demand([bronze_rows([good_row(period="2026-08-13T18")])])
    upsert_demand(conn, d)
    conn.close()
    start, _ = auto_window(settings)
    assert start == "2026-08-11T18"  # 48h before the stored hour


def test_month_start_windows():
    now = datetime(2026, 2, 15, 10, tzinfo=timezone.utc)
    assert _month_start(now, 1) == "2026-01-01T00"
    assert _month_start(now, 2) == "2025-12-01T00"
    assert _month_start(now, 13) == "2025-01-01T00"
    assert _month_start(now, 0) == "2026-02-01T00"


def test_offline_commands_run_without_the_key(tmp_path, monkeypatch):
    # transform/derive/report never touch the API — a missing key must not
    # stop them (found live: the first CI run died at transform because the
    # secret was scoped to the ingest step, correctly).
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    from gridpulse.cli import main
    assert main(["transform"]) == 0
    assert main(["derive"]) == 0
    assert main(["report", "--out", "s/index.html"]) == 0


def test_auto_window_clamps_future_dated_junk(tmp_path):
    # A single future-dated row must not invert the window (start > end)
    # and permanently stall ingestion (P3 review finding).
    settings = Settings(api_key="k", data_dir=tmp_path / "data")
    conn = connect(settings.db_path)
    d, _ = convert_demand([bronze_rows([good_row(period="2030-01-01T00")])])
    upsert_demand(conn, d)
    conn.close()
    start, end = auto_window(settings)
    fmt = "%Y-%m-%dT%H"
    assert datetime.strptime(start, fmt) < datetime.strptime(end, fmt)
