# SPDX-License-Identifier: MIT
"""Build-script tests — pure, no network, no cluster."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _fake_tree(root: Path, marker: str) -> Path:
    """Minimal fake extracted archive."""
    for sub in ("bin", "lib", "share"):
        (root / sub).mkdir(parents=True)
        (root / sub / marker).write_text(marker)
    (root / "COPYRIGHT").write_text("PostgreSQL COPYRIGHT")
    (root / "LICENSE").write_text("theseus LICENSE")
    return root


def test_checksum_mismatch_rejects(build_script, tmp_path):
    f = tmp_path / "a.tar.gz"
    f.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        build_script.verify_sha256(f, "0" * 64)


def test_checksum_match_accepts(build_script, tmp_path):
    import hashlib
    f = tmp_path / "a.tar.gz"
    f.write_bytes(b"ok")
    build_script.verify_sha256(f, hashlib.sha256(b"ok").hexdigest())


def test_target_tag_map_complete(build_script):
    checksums = json.loads((ROOT / "checksums.json").read_text())
    assert set(build_script.TARGETS) == set(checksums["sha256"])
    assert len(build_script.TARGETS) == 7
    tags = list(build_script.TARGETS.values())
    assert len(tags) == len(set(tags)), "duplicate platform tags"


def test_license_files_staged(build_script, tmp_path):
    tree = _fake_tree(tmp_path / "x", "m1")
    dest = tmp_path / "pginstall"
    build_script.stage(tree, dest)
    assert (dest / "COPYRIGHT").read_text() == "PostgreSQL COPYRIGHT"
    assert (dest / "LICENSE").read_text() == "theseus LICENSE"


def test_clean_between_targets(build_script, tmp_path):
    dest = tmp_path / "pginstall"
    build_script.stage(_fake_tree(tmp_path / "one", "first"), dest)
    build_script.stage(_fake_tree(tmp_path / "two", "second"), dest)
    assert (dest / "bin" / "second").exists()
    assert not (dest / "bin" / "first").exists(), "cross-target contamination"


def test_stage_missing_license_fails(build_script, tmp_path):
    tree = _fake_tree(tmp_path / "x", "m")
    (tree / "COPYRIGHT").unlink()
    (tree / "LICENSE").unlink()
    with pytest.raises(RuntimeError, match="license notice"):
        build_script.stage(tree, tmp_path / "pginstall")


def test_stage_windows_layout_accepted(build_script, tmp_path):
    """EDB-derived windows archives have LICENSE + *_licenses.txt, no COPYRIGHT."""
    tree = _fake_tree(tmp_path / "w", "m")
    (tree / "COPYRIGHT").unlink()
    (tree / "server_license.txt").write_text("server")
    dest = tmp_path / "pginstall"
    build_script.stage(tree, dest)
    assert (dest / "LICENSE").exists() and (dest / "server_license.txt").exists()


def test_wheel_filename_shape(build_script):
    assert (
        build_script.expected_wheel_name("17.10.0", "win_amd64")
        == "celerp_postgres-17.10.0-py3-none-win_amd64.whl"
    )
