"""Runtime settings — env-driven so CI and local runs configure identically.

A local ``.env`` file (gitignored) is read if present; real environment
variables win over it. The API key is never written anywhere by this package.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_URL = "https://api.eia.gov/v2"
DEMAND_ROUTE = "electricity/rto/region-data/data/"

# Locked in Phase 0 (see DECISIONS.md): the four target balancing authorities.
REGIONS = ("ERCO", "CISO", "MISO", "PJM")

# EIA hard-caps JSON responses at 5000 rows and warns on every response
# regardless of size (see tests/fixtures) — pagination is mandatory, and the
# warning is noise, not an error.
PAGE_LENGTH = 5000


def _read_dotenv(path: Path) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines from a .env file.

    Deliberately minimal (no quoting/expansion rules): the only thing the
    project puts in .env is the API key.
    """
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


@dataclass(frozen=True)
class Settings:
    """Immutable per-run configuration.

    ``max_requests_per_run`` is the pull budget from the build plan: a hard
    cap counted per HTTP request (retries included), enforced in the client.
    """

    # repr=False so repr(settings) in a log or traceback never prints the key.
    api_key: str = field(repr=False)
    data_dir: Path
    max_requests_per_run: int = 25
    max_retries: int = 4
    backoff_base_seconds: float = 1.0

    @property
    def bronze_dir(self) -> Path:
        return self.data_dir / "bronze"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "gridpulse.db"

    @classmethod
    def load(cls, data_dir: Path | str = "data") -> "Settings":
        """Build settings from the environment (plus ``.env`` fallback).

        Raises a message that states the fix path when the key is missing.
        """
        env = {**_read_dotenv(Path(".env")), **os.environ}
        api_key = env.get("EIA_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "EIA_API_KEY is not set. Fix: add EIA_API_KEY=<your key> to a "
                ".env file in the repo root (gitignored) or export it in the "
                "environment. Keys are free: https://www.eia.gov/opendata/register.php"
            )
        return cls(api_key=api_key, data_dir=Path(data_dir))
