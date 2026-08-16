"""Swappable dataframe backends behind one narrow interface.

The pipeline is engine-agnostic: every backend implements ``base.Backend``
and passes the same contract tests. Polars ships now; pandas is a stretch
phase implemented against the identical contract.
"""

from .base import Backend, get_backend

__all__ = ["Backend", "get_backend"]
