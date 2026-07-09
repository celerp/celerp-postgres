#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bump the bundled PostgreSQL version: rewrite checksums.json (from upstream
.sha256 sidecars — REVIEW THE DIFF, this is the trust boundary), pyproject
version, and POSTGRES_VERSION.

Usage: python scripts/update_version.py 17.11.0
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIDE = "https://github.com/theseus-rs/postgresql-binaries/releases/download/{v}/postgresql-{v}-{t}.tar.gz.sha256"


def parse_sidecar(text: str) -> str:
    """Handle both `<hash>  <file>` and Windows CertUtil multi-line format."""
    for line in text.strip().splitlines():
        m = re.fullmatch(r"([0-9a-f]{64})(\s+\S+)?", line.strip())
        if m:
            return m.group(1)
    raise ValueError(f"no sha256 found in sidecar: {text[:120]!r}")


def main() -> None:
    v = sys.argv[1]
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_wheels import TARGETS  # single source of truth for targets

    sha = {}
    for t in sorted(TARGETS):
        with urllib.request.urlopen(SIDE.format(v=v, t=t)) as r:
            sha[t] = parse_sidecar(r.read().decode())
        print(f"  {t}: {sha[t]}")
    (ROOT / "checksums.json").write_text(
        json.dumps({"version": v, "sha256": sha}, indent=2) + "\n"
    )
    for f, pat in [
        (ROOT / "pyproject.toml", r'(version = ")[^"]+(")'),
        (ROOT / "src/celerp_postgres/__init__.py", r'(POSTGRES_VERSION = ")[^"]+(")'),
    ]:
        f.write_text(re.sub(pat, rf"\g<1>{v}\g<2>", f.read_text(), count=1))
    print(f"updated to {v} — review the diff before committing.")


if __name__ == "__main__":
    main()
