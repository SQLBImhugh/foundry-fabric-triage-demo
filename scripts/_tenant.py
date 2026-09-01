"""Tenant-specific values the operational scripts need, read from the environment.

These scripts talk to a real tenant, so they need real identifiers: a tenant id,
a domain, a mailbox. Hardcoding them made the scripts work for exactly one
tenant and published that tenant's structure to anyone reading the repository.

Reading them from the environment fixes both. `required()` fails with the
variable name and what it is for, rather than letting a script run against a
half-configured environment and produce a confusing API error several calls
later.

Values come from the same `.env` the application uses, so there is one place to
configure a tenant rather than two.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    """Read .env into the environment without overriding what is already set.

    Deliberately not python-dotenv: these scripts are run ad hoc, sometimes from
    a bare interpreter, and a missing optional dependency should not stop an
    operator injecting a test alert.
    """
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def required(name: str, purpose: str) -> str:
    """Return an environment variable, or exit explaining what is missing."""
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(
            f"{name} is not set.\n"
            f"  Needed for: {purpose}\n"
            f"  Set it in .env or the current shell. See .env.example."
        )
    return value


def optional(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default
