# SPDX-License-Identifier: MIT
"""Integration: boot a real cluster from the installed wheel.

All DB access goes through the bundled psql, so the tests need zero Python
database drivers. POSIX uses a unix socket in a short temp dir; Windows uses
loopback TCP on a free port.
"""

import os
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

celerp_postgres = pytest.importorskip("celerp_postgres")
from conftest import needs_binaries

pytestmark = needs_binaries

IS_WIN = os.name == "nt"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Cluster:
    def __init__(self, tmp: Path):
        self.pgdata = tmp / "data"
        self.host = "127.0.0.1" if IS_WIN else tempfile.mkdtemp(prefix="cpg-")
        self.port = _free_port() if IS_WIN else 5432  # port unused for sockets

    def run(self, name, *args, check=True):
        return subprocess.run(
            [celerp_postgres.tool(name), *args],
            capture_output=True, text=True, check=check,
        )

    def start(self):
        if not (self.pgdata / "PG_VERSION").exists():
            self.run("initdb", "-D", str(self.pgdata), "-U", "postgres",
                     "-A", "trust", "-E", "UTF8", "--no-sync")
        opts = (
            f"-c listen_addresses=127.0.0.1 -c port={self.port}" if IS_WIN
            else f"-c listen_addresses='' -c unix_socket_directories='{self.host}'"
        )
        self.run("pg_ctl", "-D", str(self.pgdata), "-w", "-t", "60",
                 "-o", opts, "-l", str(self.pgdata / "log"), "start")

    def stop(self):
        self.run("pg_ctl", "-D", str(self.pgdata), "-w", "-m", "fast", "stop",
                 check=False)

    def psql(self, sql, db="postgres"):
        r = self.run("psql", "-h", self.host, "-p", str(self.port),
                     "-U", "postgres", "-d", db, "-tAc", sql)
        return r.stdout.strip()


@pytest.fixture()
def cluster(tmp_path):
    c = Cluster(tmp_path)
    c.start()
    yield c
    c.stop()


def test_full_lifecycle(cluster):
    assert cluster.psql("SELECT 1") == "1"
    server_v = cluster.psql("SHOW server_version").split()[0]  # e.g. "17.10"
    assert server_v == ".".join(celerp_postgres.POSTGRES_VERSION.split(".")[:2])


def test_dump_restore_roundtrip(cluster, tmp_path):
    """The reason client tools ship: a real pg_dump -> pg_restore cycle."""
    cluster.psql("CREATE DATABASE src_db")
    cluster.psql("CREATE TABLE t (v text)", db="src_db")
    cluster.psql("INSERT INTO t VALUES ('survived')", db="src_db")
    dump = tmp_path / "db.dump"
    cluster.run("pg_dump", "-h", cluster.host, "-p", str(cluster.port),
                "-U", "postgres", "-Fc", "-f", str(dump), "src_db")
    cluster.psql("CREATE DATABASE dst_db")
    cluster.run("pg_restore", "-h", cluster.host, "-p", str(cluster.port),
                "-U", "postgres", "-d", "dst_db", str(dump))
    assert cluster.psql("SELECT v FROM t", db="dst_db") == "survived"


def test_restart_preserves_data(tmp_path):
    c = Cluster(tmp_path)
    c.start()
    try:
        c.psql("CREATE TABLE keep (v int)")
        c.psql("INSERT INTO keep VALUES (42)")
        c.stop()
        c.start()
        assert c.psql("SELECT v FROM keep") == "42"
    finally:
        c.stop()
