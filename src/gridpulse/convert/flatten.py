"""Bronze documents → an ordered stream of (raw_row, fetched_at) pairs."""

from __future__ import annotations

from typing import Iterator


def iter_rows(bronze_docs: list[dict]) -> Iterator[tuple[dict, str]]:
    """Yield every payload row with its fetch stamp, oldest fetch first.

    The ordering is load-bearing: EIA revises past hours, and downstream
    survivorship (dedupe here, upsert guards in storage) assumes later
    fetches arrive later in the stream.
    """
    for doc in sorted(bronze_docs, key=lambda d: d["fetched_at"]):
        fetched_at = doc["fetched_at"]
        for raw_row in doc["payload"]["response"]["data"]:
            yield raw_row, fetched_at
