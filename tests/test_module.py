# SPDX-License-Identifier: MIT
"""Pure module tests — need the installed wheel for path checks, no cluster."""

import os
from pathlib import Path

import pytest

celerp_postgres = pytest.importorskip("celerp_postgres")
from conftest import needs_binaries

TOOLS = ["postgres", "initdb", "pg_ctl", "psql", "pg_dump", "pg_restore"]


def test_version_consistency():
    from importlib.metadata import version
    assert version("celerp-postgres") == celerp_postgres.POSTGRES_VERSION


@needs_binaries
def test_bin_dir_exists_and_is_dir():
    assert Path(celerp_postgres.bin_dir()).is_dir()


@needs_binaries
@pytest.mark.parametrize("name", TOOLS)
def test_tool_resolves_all_six(name):
    p = Path(celerp_postgres.tool(name))
    assert p.is_file()
    if os.name != "nt":
        assert os.access(p, os.X_OK)


def test_tool_missing_raises_filenotfound():
    with pytest.raises(FileNotFoundError, match="definitely_not_a_pg_tool"):
        celerp_postgres.tool("definitely_not_a_pg_tool")


def test_candidates_appends_exe_on_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    assert celerp_postgres._candidates("pg_dump") == ["pg_dump.exe", "pg_dump"]
    monkeypatch.setattr(os, "name", "posix")
    assert celerp_postgres._candidates("pg_dump") == ["pg_dump"]
