"""EIA API client: paginated fetches, retry/backoff, and the pull budget.

The budget is a hard cap, not advice: every HTTP request (retries included)
counts against ``Settings.max_requests_per_run``, and hitting the cap aborts
the run with an error that states the fix path. Downstream stages never touch
the API — this module is the only network code in the package.
"""

from __future__ import annotations

import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx
from pydantic import ValidationError

from .config import BASE_URL, DEMAND_ROUTE, FUELMIX_ROUTE, PAGE_LENGTH, REGIONS, Settings
from .schemas.raw import RawResponseMeta

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class PullBudgetExceeded(RuntimeError):
    """Raised when a run tries to make more requests than the budget allows.

    Carries ``pages``: payloads fetched before the cap hit, so callers can
    persist partial work instead of re-spending budget on it. Instance
    state, never a class-level default — a shared class list would leak
    pages across raise sites.
    """

    def __init__(self, message: str, pages: list[dict] | None = None) -> None:
        super().__init__(message)
        self.pages: list[dict] = pages if pages is not None else []


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

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> "EiaClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

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
            if resp.status_code >= 400:
                # Never raise_for_status(): httpx embeds the full URL — key
                # included — in its message, and 401 is exactly the status an
                # invalid key produces (review finding MED-1).
                raise RuntimeError(
                    f"EIA returned {resp.status_code} for {route}. "
                    "Fix: 401/403 means the key is invalid — check EIA_API_KEY; "
                    "400 means bad params — inspect the window arguments."
                )
            return resp.json()

    def fetch_demand_window(self, start: str, end: str) -> list[dict]:
        """Fetch hourly demand for all target regions over [start, end]."""
        return self._fetch_window(DEMAND_ROUTE, start, end, {"facets[type][]": "D"})

    def fetch_fuelmix_window(self, start: str, end: str) -> list[dict]:
        """Fetch hourly generation by fuel type for all target regions.

        No fueltype facet: the vocabulary differs per region (pinned
        fixture), so filtering server-side would silently drop fuels.
        """
        return self._fetch_window(FUELMIX_ROUTE, start, end, {})

    def _fetch_window(
        self, route: str, start: str, end: str, extra_facets: dict
    ) -> list[dict]:
        """Shared paginated fetch over [start, end].

        Returns the verbatim response payloads, one per page. The offset
        advances by rows actually received — NEVER by the requested page
        length or the server total, which is a string and reports the full
        result set, not this page. The generic "incomplete return" warning on
        every response is noise and deliberately ignored.

        If the pull budget dies mid-window, pages already fetched are
        attached to the exception (``exc.pages``) so the caller can persist
        what was paid for.
        """
        params = {
            "frequency": "hourly",
            "data[0]": "value",
            "start": start,
            "end": end,
            "length": PAGE_LENGTH,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            **extra_facets,
        }
        for i, region in enumerate(REGIONS):
            params[f"facets[respondent][{i}]"] = region

        pages: list[dict] = []
        offset = 0
        while True:
            try:
                page = self._get(route, {**params, "offset": offset})
            except PullBudgetExceeded as exc:
                exc.pages = pages  # budget spent — don't discard what it bought
                raise
            pages.append(page)
            # Pydantic at the boundary applies to the envelope too: a
            # renamed/missing key must fail with a fix path, not a KeyError.
            # RawResponseMeta.total coerces the wire's string form.
            try:
                meta = RawResponseMeta.model_validate(page.get("response") or {})
            except ValidationError as exc:
                raise RuntimeError(
                    f"EIA response envelope unrecognized at offset {offset} "
                    f"({exc.error_count()} field errors). Fix: the API shape may "
                    "have changed — inspect the raw page and update schemas/raw.py."
                ) from None
            offset += len(meta.data)
            if offset >= meta.total or not meta.data:
                return pages


def write_bronze(
    pages: list[dict], run_id: str, bronze_dir: Path, window: str, dataset: str = "demand"
) -> list[Path]:
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
        # Gzipped, compact JSON: the repo data budget (~50 MB) assumes
        # compressed raw files — plain indented JSON is ~10x larger.
        path = bronze_dir / f"{dataset}_{window}_p{i:03d}_{run_id}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(out, f, separators=(",", ":"))
        written.append(path)
    return written
