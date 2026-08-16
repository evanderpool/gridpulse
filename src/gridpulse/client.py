"""EIA API client: paginated fetches, retry/backoff, and the pull budget.

The budget is a hard cap, not advice: every HTTP request (retries included)
counts against ``Settings.max_requests_per_run``, and hitting the cap aborts
the run with an error that states the fix path. Downstream stages never touch
the API — this module is the only network code in the package.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from .config import BASE_URL, DEMAND_ROUTE, PAGE_LENGTH, REGIONS, Settings

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class PullBudgetExceeded(RuntimeError):
    """Raised when a run tries to make more requests than the budget allows."""


class EiaClient:
    """Thin, budgeted wrapper over the EIA v2 REST API.

    ``transport`` and ``sleep`` are injectable so tests run offline and
    without real backoff delays.
    """

    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._client = httpx.Client(base_url=BASE_URL, transport=transport, timeout=30.0)
        self._sleep = sleep
        self.requests_made = 0

    def _get(self, route: str, params: dict) -> dict:
        """One budgeted, retried GET returning parsed JSON."""
        attempts = 0
        while True:
            if self.requests_made >= self.settings.max_requests_per_run:
                raise PullBudgetExceeded(
                    f"pull budget hit: {self.requests_made} requests made, cap is "
                    f"{self.settings.max_requests_per_run}. Fix: narrow the window "
                    "(--start/--end) or raise max_requests_per_run deliberately."
                )
            self.requests_made += 1
            attempts += 1
            try:
                resp = self._client.get(route, params={**params, "api_key": self.settings.api_key})
            except httpx.TransportError as exc:
                if attempts > self.settings.max_retries:
                    raise RuntimeError(
                        f"EIA request failed after {attempts} attempts ({exc!r}). "
                        "Fix: check connectivity, then re-run — bronze is append-only, "
                        "so a re-run cannot corrupt anything."
                    ) from exc
                self._sleep(self.settings.backoff_base_seconds * 2 ** (attempts - 1))
                continue
            if resp.status_code in RETRYABLE_STATUS:
                if attempts > self.settings.max_retries:
                    raise RuntimeError(
                        f"EIA kept returning {resp.status_code} after {attempts} attempts. "
                        "Fix: wait and re-run; if 429, the schedule may be too aggressive."
                    )
                self._sleep(self.settings.backoff_base_seconds * 2 ** (attempts - 1))
                continue
            resp.raise_for_status()
            return resp.json()

    def fetch_demand_window(self, start: str, end: str) -> list[dict]:
        """Fetch hourly demand for all target regions over [start, end].

        Returns the verbatim response payloads, one per page. Pagination uses
        the server-reported total; the generic "incomplete return" warning on
        every response is noise and deliberately ignored.
        """
        params = {
            "frequency": "hourly",
            "data[0]": "value",
            "facets[type][]": "D",
            "start": start,
            "end": end,
            "length": PAGE_LENGTH,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
        }
        for i, region in enumerate(REGIONS):
            params[f"facets[respondent][{i}]"] = region

        pages: list[dict] = []
        offset = 0
        while True:
            page = self._get(DEMAND_ROUTE, {**params, "offset": offset})
            pages.append(page)
            total = int(page["response"]["total"])
            offset += len(page["response"]["data"])
            if offset >= total or not page["response"]["data"]:
                return pages


def write_bronze(pages: list[dict], run_id: str, bronze_dir: Path, window: str) -> list[Path]:
    """Persist verbatim payloads, stamped with fetch time — never the API key.

    EIA echoes request params (key included) back inside the payload, so the
    echo is scrubbed here at write time; everything else is byte-faithful.
    """
    bronze_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat()
    written: list[Path] = []
    for i, page in enumerate(pages):
        doc = dict(page)
        if "request" in doc and "params" in doc.get("request", {}):
            doc["request"] = {**doc["request"], "params": {**doc["request"]["params"]}}
            doc["request"]["params"].pop("api_key", None)
        out = {"fetched_at": fetched_at, "window": window, "payload": doc}
        path = bronze_dir / f"demand_{window}_p{i:03d}_{run_id}.json"
        path.write_text(json.dumps(out, indent=1), encoding="utf-8", newline="\n")
        written.append(path)
    return written
