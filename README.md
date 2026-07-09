# celerp-postgres

**PostgreSQL, pip installable.** A full PostgreSQL distribution — the server *and* the
client tools — delivered as a platform wheel. No apt/brew/installer, no Docker, no root,
no compilation.

```bash
pip install celerp-postgres
```

That's the whole setup. You get unmodified PostgreSQL binaries on disk and a
three-function API that tells you where they are. What you run with them is up to you.

## Quickstart

Spin up a real PostgreSQL 17 database in a few lines — no drivers required beyond
whatever you already use to talk to Postgres:

```python
import subprocess, tempfile
from celerp_postgres import tool

data = tempfile.mkdtemp()

# One-time cluster init (no password prompts: local trust auth)
subprocess.run([tool("initdb"), "-D", data, "-U", "postgres", "-A", "trust"], check=True)

# Start on localhost:54321
subprocess.run([tool("pg_ctl"), "-D", data, "-w",
                "-o", "-c listen_addresses=127.0.0.1 -c port=54321",
                "-l", f"{data}/log", "start"], check=True)

# ... connect with psycopg / asyncpg / SQLAlchemy / anything:
#     postgresql://postgres@127.0.0.1:54321/postgres

subprocess.run([tool("pg_ctl"), "-D", data, "-w", "stop"], check=True)
```

On Linux/macOS you can skip TCP entirely and listen on a unix socket
(`-c listen_addresses='' -c unix_socket_directories=<dir>`), which keeps the database
invisible to the network.

## What's in the box

Every wheel contains the complete distribution for its platform:

| | |
|---|---|
| **Server** | `postgres`, `initdb`, `pg_ctl` |
| **Client tools** | `psql`, `pg_dump`, `pg_restore`, `pg_basebackup`, `createdb`, … |
| **Runtime** | `lib/` (shared libraries), `share/` (initdb templates, timezone data) |
| **Licenses** | PostgreSQL `COPYRIGHT` + upstream `LICENSE`, inside the package |

C headers (`include/`) are not shipped — this package runs PostgreSQL; it isn't a
build-against-libpq SDK.

### Supported platforms

| Wheel | Platform |
|---|---|
| `manylinux_2_34_x86_64` / `_aarch64` | Linux (glibc 2.34+) |
| `musllinux_1_2_x86_64` / `_aarch64` | Alpine / musl Linux |
| `macosx_10_15_x86_64`, `macosx_11_0_arm64` | macOS Intel / Apple Silicon |
| `win_amd64` | Windows x64 |

Any CPython ≥ 3.9. Wheels only — there is deliberately no sdist, because a source
install could not contain the binaries and would silently produce a broken package.

The glibc, macOS, and Windows wheels are fully self-contained. The Alpine/musl wheels
additionally need a handful of runtime libraries from apk:
`apk add icu-libs libxml2 zstd-libs lz4-libs krb5-libs readline`.

## API

Three symbols; that's the entire surface:

```python
from celerp_postgres import POSTGRES_VERSION, bin_dir, tool

POSTGRES_VERSION   # "17.10.0" — the bundled PostgreSQL version (== package version)
bin_dir()          # ".../site-packages/celerp_postgres/pginstall/bin"
tool("pg_dump")    # full path to a tool; raises FileNotFoundError if absent
```

No lifecycle magic, no hidden daemons, no atexit hooks. You own the process — which is
exactly what makes this composable with whatever supervisor, test fixture, or app
framework you already have.

## Why you'd want this

- **Test suites** that need a real PostgreSQL without Docker, CI services, or a
  system install — `pip install`, boot per-session, throw away.
- **Local-first / desktop apps** that embed a private database next to the app's data
  directory instead of asking users to install a server.
- **CLI tools and data scripts** that need `pg_dump`/`pg_restore`/`psql` at a known,
  version-pinned path regardless of what the host has on `PATH`.
- **Air-gapped and locked-down environments** — everything arrives through pip's
  normal, hash-verifiable channel; nothing is downloaded at runtime.
- **Teaching, notebooks, demos** — a real database with zero setup instructions.

## Versioning & updates

The package version **is** the PostgreSQL version (`17.10.0` ships PostgreSQL 17.10).
New PostgreSQL point releases are published as new package versions shortly after the
upstream release train; pin `>=17.10,<18` to receive security patches within the major
you initialized your data directory with. Repackaging-only fixes use post releases
(`17.10.0.post1`). PostgreSQL major upgrades change the on-disk `pgdata` format —
moving 17 → 18 requires a `pg_dump`/`pg_restore` migration, as with any PostgreSQL
installation.

## How it's built (and why you can trust it)

Wheels repackage the excellent
[theseus-rs/postgresql-binaries](https://github.com/theseus-rs/postgresql-binaries)
release archives — the same binary source used across the Rust embedded-postgres
ecosystem. Every archive's sha256 is **pinned in this repository** and verified at
build time (never trusted from a live sidecar), the binaries are shipped unmodified,
and every release is gated on CI that boots a real cluster on each platform and runs a
`pg_dump` → `pg_restore` roundtrip before anything reaches PyPI. Publishing uses PyPI
trusted publishing (OIDC) — no long-lived credentials exist.

## License

- **Package code** (accessor module + build tooling): [MIT](LICENSE).
- **PostgreSQL binaries**: [PostgreSQL License](https://www.postgresql.org/about/licence/)
  — permissive, OSI-approved; the `COPYRIGHT` and upstream `LICENSE` notices ship inside
  the package at `celerp_postgres/pginstall/`.

Credits: the [PostgreSQL Global Development Group](https://www.postgresql.org/), and
[theseus-rs](https://github.com/theseus-rs/postgresql-binaries) for the portable builds.

---

Maintained by the [Celerp](https://github.com/celerp) team.
