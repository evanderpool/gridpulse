"""The API key must never reach log output.

Found live during the first real run: httpx logs full request URLs (key
included) at INFO level. In GitHub Actions those logs are public.
"""

import json
import logging
from pathlib import Path

import httpx

from gridpulse.cli import cmd_ingest, main
from gridpulse.client import EiaClient
from gridpulse.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


def test_httpx_logger_is_silenced_by_main(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no .env here → main exits on missing key
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    try:
        main(["transform"])
    except RuntimeError:
        pass
    assert logging.getLogger("httpx").level == logging.WARNING


def test_ingest_logs_never_contain_the_key(tmp_path, caplog):
    payload = json.loads((FIXTURES / "demand_hourly_4regions.json").read_text(encoding="utf-8"))
    payload["response"]["total"] = len(payload["response"]["data"])
    settings = Settings(api_key="sekrit-key-value", data_dir=tmp_path / "data")
    client = EiaClient(
        settings,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)),
    )
    with caplog.at_level(logging.DEBUG):
        cmd_ingest(settings, "a", "b", client=client)
    assert "sekrit-key-value" not in caplog.text
