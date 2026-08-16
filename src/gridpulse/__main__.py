"""``python -m gridpulse`` dispatches to the CLI."""

import sys

from .cli import main

sys.exit(main())
