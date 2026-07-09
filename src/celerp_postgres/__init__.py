# SPDX-License-Identifier: MIT
"""celerp-postgres: a full PostgreSQL distribution (server + client tools),
installed by pip as a platform wheel.

This package contains no lifecycle logic by design — it hands you paths to
unmodified PostgreSQL binaries (`initdb`, `pg_ctl`, `postgres`, `psql`,
`pg_dump`, `pg_restore`) and gets out of the way. See the README for a
copy-paste quickstart.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Version of the bundled PostgreSQL distribution (== the package version).
POSTGRES_VERSION = "17.10.0"

_PGINSTALL = Path(__file__).parent / "pginstall"


def bin_dir() -> str:
    """Directory containing the bundled PostgreSQL executables."""
    return str(_PGINSTALL / "bin")


def _candidates(name: str) -> list[str]:
    """Executable filename candidates for `name` on this OS."""
    return [f"{name}.exe", name] if os.name == "nt" else [name]


def icu_data_dir() -> str | None:
    """Directory holding bundled ICU data, or None on platforms that don't
    need it. On musl/Alpine wheels, set ICU_DATA to this before running
    initdb/postgres (Alpine's ICU reads its data from an external file)."""
    d = _PGINSTALL / "share" / "icu"
    return str(d) if d.is_dir() else None


def tool(name: str) -> str:
    """Full path to a bundled tool (e.g. 'initdb', 'pg_ctl', 'pg_dump').

    Raises FileNotFoundError with the searched location if absent.
    """
    for cand in _candidates(name):
        p = _PGINSTALL / "bin" / cand
        if p.is_file():
            return str(p)
    raise FileNotFoundError(
        f"{name} not found in {_PGINSTALL / 'bin'} — is celerp-postgres "
        "installed from a platform wheel (not a source checkout)?"
    )
