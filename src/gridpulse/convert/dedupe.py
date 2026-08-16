"""In-batch deduplication with the survivorship rule: latest fetch wins.

Cross-run survivorship lives in storage's guarded upserts; this module
resolves duplicates *within* one conversion batch (the same hour appearing
in overlapping fetch windows) so a batch presents one candidate per key.
"""

from __future__ import annotations

from typing import Callable, Hashable, TypeVar

R = TypeVar("R")


def dedupe_latest(records: list[R], key: Callable[[R], Hashable]) -> list[R]:
    """Keep the last-seen record per key, preserving first-seen key order.

    Input arrives in fetched_at order (flatten guarantees it), so last-seen
    IS the latest fetch; on equal stamps the later stream position wins,
    identically on every replay — determinism is what makes the transform's
    idempotency provable.
    """
    by_key: dict[Hashable, R] = {}
    for record in records:
        by_key[key(record)] = record
    return list(by_key.values())
