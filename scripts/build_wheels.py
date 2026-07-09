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
    # theseus mac binaries are built with LC_BUILD_VERSION minos 26.0 (measured
    # via macholib) — tag honestly so pip never selects them on older macOS.
    "x86_64-apple-darwin": "macosx_26_0_x86_64",
    "aarch64-apple-darwin": "macosx_26_0_arm64",
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


# ── Linux self-containment: prune + graft ─────────────────────────────────────
#
# theseus linux binaries dynamically link distro libs (openssl, libxml2, krb5,
# zstd, lz4, icu on musl, ...). Full distros have them; minimal images (debian
# -slim, alpine) do not. We make the wheels genuinely self-contained by grafting
# the missing closure into pginstall/lib (binaries already carry RUNPATH
# $ORIGIN/../lib) and RPATH-patching every grafted lib. Sources: AlmaLinux 9
# packages for gnu (glibc 2.34 == our tag floor), Alpine 3.20 for musl.

# Extension PLUGIN files whose dependency trees we refuse to ship (LLVM ~100MB,
# host python, legacy uuid, xslt). Exact basenames only — a loose "*xml2*" glob
# once deleted libxml2.dll/dylib, which the server itself links.
PRUNE_LIBS = re.compile(
    # plugin basenames only — never `lib*` runtime libraries (libxml2.dll is the
    # server's own dependency; xml2.so is the prunable contrib extension).
    r"^(?!lib)([\w]*(llvmjit|plpython3|uuid-ossp|pgxml|xslt)[\w-]*|xml2)\.(so|dll|dylib)$"
)
PRUNE_SHARE = re.compile(r"^(uuid-ossp|xml2|plpython)")
# Never grafted: guaranteed by the platform baseline.
BASE_SONAMES = re.compile(
    r"^(libc\.so|libm\.so|libdl\.so|libpthread\.so|librt\.so|libutil\.so"
    r"|libresolv\.so|libnsl\.so|ld-linux.*|libc\.musl.*)"
)
ALMA = "https://repo.almalinux.org/almalinux/9/{repo}/{arch}/os/"
ALPINE = "https://dl-cdn.alpinelinux.org/alpine/v3.20/{repo}/{arch}/"


def _elf_needed(f: Path) -> list[str]:
    out = subprocess.run(["readelf", "-d", str(f)], capture_output=True, text=True).stdout
    return re.findall(r"NEEDED.*\[([^\]]+)\]", out)


def _is_elf(f: Path) -> bool:
    try:
        return f.is_file() and not f.is_symlink() and open(f, "rb").read(4) == b"\x7fELF"
    except OSError:
        return False


def prune_extensions(tree: Path) -> None:
    for f in list(tree.rglob("*")):
        if f.is_file() and (
            PRUNE_LIBS.match(f.name)
            or ("extension" in f.parts and PRUNE_SHARE.match(f.name))
        ):
            f.unlink(missing_ok=True)
    shutil.rmtree(tree / "lib" / "bitcode", ignore_errors=True)  # llvmjit bitcode


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as r:
        return r.read()


def _alma_index(arch: str) -> dict[str, str]:
    """soname -> package download URL, from Alma 9 BaseOS+AppStream repodata."""
    import gzip
    import xml.etree.ElementTree as ET

    idx: dict[str, str] = {}
    for repo in ("BaseOS", "AppStream"):
        base = ALMA.format(repo=repo, arch=arch)
        md = ET.fromstring(_fetch(base + "repodata/repomd.xml"))
        ns = {"r": "http://linux.duke.edu/metadata/repo"}
        href = next(
            d.find("r:location", ns).get("href")
            for d in md.findall("r:data", ns) if d.get("type") == "primary"
        )
        pri = ET.fromstring(gzip.decompress(_fetch(base + href)))
        cns = {"c": "http://linux.duke.edu/metadata/common",
               "rpm": "http://linux.duke.edu/metadata/rpm"}
        for pkg in pri.findall("c:package", cns):
            if pkg.find("c:arch", cns).text != arch:
                continue  # the x86_64 repo also carries i686 multilib packages
            loc = pkg.find("c:location", cns).get("href")
            fmt = pkg.find("c:format", cns)
            for prov in fmt.findall("rpm:provides/rpm:entry", cns) if fmt is not None else []:
                name = prov.get("name", "")
                if ".so" in name:
                    idx.setdefault(name.split("(")[0], base + loc)
    return idx


def _alpine_index(arch: str) -> dict[str, str]:
    """soname -> .apk download URL, from Alpine APKINDEX provides (so: entries)."""
    import io

    idx: dict[str, str] = {}
    for repo in ("main", "community"):
        base = ALPINE.format(repo=repo, arch=arch)
        raw = _fetch(base + "APKINDEX.tar.gz")
        with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
            text = tf.extractfile("APKINDEX").read().decode()
        for block in text.split("\n\n"):
            fields = dict(line.split(":", 1) for line in block.splitlines() if ":" in line)
            if "P" not in fields:
                continue
            url = f"{base}{fields['P']}-{fields['V']}.apk"
            for p in fields.get("p", "").split():
                if p.startswith("so:"):
                    idx.setdefault(p[3:].split("=")[0], url)
    return idx


def _iter_pkg_libs(data: bytes, url: str):
    """Yield (member_name, bytes) for lib files inside an .rpm or .apk."""
    import io
    import zlib

    if url.endswith(".apk"):
        # An .apk is 2-3 concatenated gzip streams; the last holds the payload.
        off, streams = 0, []
        while off < len(data):
            d = zlib.decompressobj(wbits=31)
            streams.append(d.decompress(data[off:]))
            off = len(data) - len(d.unused_data)
            if not d.unused_data:
                break
        with tarfile.open(fileobj=io.BytesIO(streams[-1])) as tf:
            for m in tf:
                if m.isfile() and "/lib" in f"/{m.name}":
                    data_ = tf.extractfile(m).read()
                    if data_[:4] == b"\x7fELF":  # skip symlink/text entries
                        yield Path(m.name).name, data_
    else:  # rpm
        import rpmfile

        with rpmfile.open(fileobj=io.BytesIO(data)) as rf:
            for m in rf.getmembers():
                if "/lib" in f"/{m.name}":
                    fo = rf.extractfile(m)
                    if fo is not None:
                        data_ = fo.read()
                        if data_[:4] == b"\x7fELF":  # skip symlink/text entries
                            yield Path(m.name).name, data_


def graft_linux(tree: Path, target: str) -> None:
    """Copy every non-base NEEDED soname into tree/lib and RPATH-patch it,
    iterating until the closure is complete. Hard-fails if anything stays
    unresolved — an incomplete closure must never ship."""
    arch = "aarch64" if target.startswith("aarch64") else "x86_64"
    index = _alpine_index(arch) if "musl" in target else _alma_index(arch)
    pkg_cache: dict[str, dict[str, bytes]] = {}

    # Normalize RPATH on EVERY linux ELF, not just grafted ones: transitive deps
    # (initdb -> libpq -> libgssapi) resolve via the DEPENDING lib's runpath, and
    # upstream libs like libpq ship without one — on minimal images the lookup
    # then falls through to (empty) system dirs.
    for f in tree.rglob("*"):
        if _is_elf(f):
            rp = "$ORIGIN/../lib" if f.parent.name == "bin" else "$ORIGIN"
            subprocess.run(["patchelf", "--force-rpath", "--set-rpath", rp, str(f)],
                           check=True, capture_output=True)

    for _round in range(6):
        have = {f.name for f in (tree / "lib").iterdir()}
        missing: set[str] = set()
        for f in [*tree.rglob("*")]:
            if _is_elf(f):
                for n in _elf_needed(f):
                    if n not in have and not BASE_SONAMES.match(n):
                        missing.add(n)
        if not missing:
            return
        for so in sorted(missing):
            url = index.get(so)
            if url is None:
                raise RuntimeError(f"{target}: no package provides {so}")
            if url not in pkg_cache:
                print(f"   graft: {so} <- {url.rsplit('/', 1)[1]}")
                pkg_cache[url] = dict(_iter_pkg_libs(_fetch(url), url))
            # Prefer exact soname; else the real file it links to (lib*.so.x.y.z).
            libs = pkg_cache[url]
            src = libs.get(so) or next(
                (v for k, v in sorted(libs.items()) if k.startswith(so)), None)
            if src is None:
                raise RuntimeError(f"{so} not found inside {url}")
            dest = tree / "lib" / so
            dest.write_bytes(src)
            dest.chmod(0o755)
            subprocess.run(["patchelf", "--force-rpath", "--set-rpath", "$ORIGIN",
                            str(dest)], check=True, capture_output=True)
    raise RuntimeError(f"{target}: graft closure did not converge")


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
        prune_extensions(PGINSTALL)
        if "linux" in target:
            graft_linux(PGINSTALL, target)
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
