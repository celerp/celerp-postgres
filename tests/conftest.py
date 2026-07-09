# SPDX-License-Identifier: MIT
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def build_script():
    """Load scripts/build_wheels.py as a module without importing side effects."""
    spec = importlib.util.spec_from_file_location(
        "build_wheels", ROOT / "scripts" / "build_wheels.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def has_binaries() -> bool:
    try:
        import celerp_postgres
        return Path(celerp_postgres.bin_dir()).is_dir()
    except Exception:
        return False


needs_binaries = pytest.mark.skipif(
    not has_binaries(), reason="platform wheel not installed (source checkout)"
)
