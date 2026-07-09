#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build celerp-postgres platform wheels by repackaging theseus-rs/postgresql-binaries.

No compilation. For each target: download the release archive, verify its sha256
against the PINNED value in checksums.json (never a fetched sidecar), stage
bin/lib/share + license files into src/celerp_postgres/pginstall/, build a pure
wheel, retag it with the target's platform tag.

Usage:
  python scripts/build_wheels.py --all
  python scripts/build_wheels.py --target x86_64-unknown-linux-gnu
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PGINSTALL = ROOT / "src" / "celerp_postgres" / "pginstall"
DIST = ROOT / "dist"
CACHE = DIST / "_downloads"

# target triple -> wheel platform tag.
# manylinux floor MEASURED from the binaries (readelf max GLIBC_ symbol = 2.34);
# _assert_glibc_floor re-verifies on every build so a silent upstream toolchain
# bump can never ship under a stale tag.
TARGETS: dict[str, str] = {
    "x86_64-unknown-linux-gnu": "manylinux_2_34_x86_64",
    "aarch64-unknown-linux-gnu": "manylinux_2_34_aarch64",
    "x86_64-unknown-linux-musl": "musllinux_1_2_x86_64",
    "aarch64-unknown-linux-musl": "musllinux_1_2_aarch64",
    "x86_64-apple-darwin": "macosx_10_15_x86_64",
    "aarch64-apple-darwin": "macosx_11_0_arm64",
    "x86_64-pc-windows-msvc": "win_amd64",
}
URL = "https://github.com/theseus-rs/postgresql-binaries/releases/download/{v}/postgresql-{v}-{t}.tar.gz"
# Shipped subdirs. `include/` (C headers, ~8 MB) is deliberately dropped: this
# package runs PostgreSQL, it doesn't compile extensions against it.
SUBDIRS = ("bin", "lib", "share")
# License/notice files vary by target: POSIX archives ship COPYRIGHT + LICENSE at
# the root; the windows (EDB-derived) archive ships LICENSE, server_license.txt and
# *_3rd_party_licenses.txt. Ship every root-level notice file we find; at least one
# must exist or the build fails.
LICENSE_GLOBS = ("COPYRIGHT", "LICENSE", "*license*.txt", "*licenses*.txt")


def load_checksums() -> dict:
    return json.loads((ROOT / "checksums.json").read_text())


def verify_sha256(path: Path, expected: str) -> None:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != expected:
        raise RuntimeError(
            f"sha256 mismatch for {path.name}: got {h.hexdigest()}, pinned {expected}"
        )


def download(version: str, target: str, expected: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / f"postgresql-{version}-{target}.tar.gz"
    if not dest.exists():
        url = URL.format(v=version, t=target)
        print(f"  downloading {url}")
        with urllib.request.urlopen(url) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    verify_sha256(dest, expected)
    return dest


def extract(archive: Path, workdir: Path) -> Path:
    """Extract archive; return the single top-level directory."""
    with tarfile.open(archive) as tf:
        try:
            tf.extractall(workdir, filter="data")  # py>=3.10.12
        except TypeError:  # older interpreters
            tf.extractall(workdir)
    tops = [p for p in workdir.iterdir() if p.is_dir()]
    if len(tops) != 1:
        raise RuntimeError(f"expected one top-level dir in {archive.name}, got {tops}")
    return tops[0]


def stage(extracted: Path, dest: Path = PGINSTALL) -> None:
    """Populate dest with bin/lib/share + license files. Wipes dest first so
    consecutive targets can never cross-contaminate."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for sub in SUBDIRS:
        src = extracted / sub
        if not src.is_dir():
            raise RuntimeError(f"archive missing {sub}/ — layout changed upstream?")
        shutil.copytree(src, dest / sub, symlinks=False)
    found = set()
    for pat in LICENSE_GLOBS:
        for src in extracted.glob(pat):
            if src.is_file():
                shutil.copy2(src, dest / src.name)
                found.add(src.name)
    if not found:
        raise RuntimeError("archive has no root license notice — license notice must ship in the wheel")


def _assert_glibc_floor(tree: Path, plat_tag: str) -> None:
    """For manylinux targets: measured max GLIBC_x.y must be <= the tag floor."""
    m = re.match(r"manylinux_(\d+)_(\d+)_", plat_tag)
    if not m:
        return
    floor = (int(m.group(1)), int(m.group(2)))
    seen: set[tuple[int, int]] = set()
    for f in list((tree / "bin").iterdir()) + list((tree / "lib").rglob("*.so*")):
        if not f.is_file():
            continue
        out = subprocess.run(["readelf", "--dyn-syms", str(f)],
                             capture_output=True, text=True).stdout
        for g in re.findall(r"GLIBC_(\d+)\.(\d+)", out):
            seen.add((int(g[0]), int(g[1])))
    if seen and max(seen) > floor:
        raise RuntimeError(f"glibc floor {max(seen)} exceeds tag {plat_tag} — retag needed")


def expected_wheel_name(version: str, plat_tag: str) -> str:
    return f"celerp_postgres-{version}-py3-none-{plat_tag}.whl"


def build_one(version: str, target: str, checksums: dict) -> Path:
    plat = TARGETS[target]
    print(f"== {target} -> {plat}")
    archive = download(version, target, checksums["sha256"][target])
    with tempfile.TemporaryDirectory() as td:
        extracted = extract(archive, Path(td))
        stage(extracted)
        _assert_glibc_floor(PGINSTALL, plat)
        # setuptools reuses build/ across runs and never REMOVES files there —
        # without this wipe, target N's wheel would contain target N-1's binaries.
        shutil.rmtree(ROOT / "build", ignore_errors=True)
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--no-isolation",
             "--outdir", str(DIST)],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        generic = DIST / f"celerp_postgres-{version}-py3-none-any.whl"
        subprocess.run(
            [sys.executable, "-m", "wheel", "tags", "--python-tag", "py3",
             "--abi-tag", "none", "--platform-tag", plat, "--remove", str(generic)],
            check=True, capture_output=True, text=True,
        )
    shutil.rmtree(PGINSTALL, ignore_errors=True)  # leave the source tree clean
    out = DIST / expected_wheel_name(version, plat)
    if not out.exists():
        raise RuntimeError(f"expected wheel not produced: {out.name}")
    print(f"   built {out.name} ({out.stat().st_size >> 20} MB)")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--target", choices=sorted(TARGETS))
    args = ap.parse_args()

    checksums = load_checksums()
    version = checksums["version"]
    pyproject_v = re.search(r'version = "([^"]+)"', (ROOT / "pyproject.toml").read_text()).group(1)
    if version != pyproject_v:
        raise SystemExit(f"checksums.json version {version} != pyproject {pyproject_v}")

    targets = sorted(TARGETS) if args.all else [args.target]
    for t in targets:
        build_one(version, t, checksums)
    print(f"done: {len(targets)} wheel(s) in {DIST}")


if __name__ == "__main__":
    main()
