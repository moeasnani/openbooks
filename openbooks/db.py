"""Database backends — the engine seam.

The query layer (:mod:`openbooks.queries`) writes engine-neutral SQL with
``?`` positional placeholders. A backend's job is only to:

  1. hold a connection,
  2. run a query and hand back ``list[dict]`` with JSON-safe values,
  3. translate the placeholder style if its driver needs it.

Two implementations ship:

* :class:`DuckDBBackend` — the embedded engine used locally and by the
  ``sql/`` build pipeline. Native ``?`` placeholders, nothing translated.
* :class:`PostgresBackend` — for server deployment. Translates ``?`` to
  ``%s`` (psycopg style) and otherwise behaves identically.

Both engines accept the ANSI-portable SQL emitted by the query layer
(``coalesce``, ``filter (where …)``, window functions, ``regexp_replace``
with the ``'g'`` flag). DuckDB-only sugar is confined to
:mod:`openbooks.bootstrap`, which documents its Postgres equivalents.
"""

from __future__ import annotations

import datetime as _dt
import decimal as _decimal
from collections.abc import Sequence
from typing import Any, Protocol


def _json_safe(value: Any) -> Any:
    """Coerce driver-native scalar types to JSON-serializable ones."""
    if isinstance(value, _decimal.Decimal):
        return float(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    return value


def rows_to_dicts(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[dict]:
    """Zip cursor rows into JSON-safe dicts."""
    return [
        {col: _json_safe(val) for col, val in zip(columns, row, strict=True)}
        for row in rows
    ]


class Backend(Protocol):
    """What the query layer requires of an engine."""

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        """Run a SELECT; return rows as JSON-safe dicts."""
        ...

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        """Run a statement for its side effects (DDL / DML)."""
        ...

    def close(self) -> None: ...


class DuckDBBackend:
    """Embedded DuckDB engine.

    Parameters
    ----------
    db_path:
        Path to the ``.duckdb`` file.
    read_only:
        Open mode. The query layer defaults to read-only and only asks
        for a writable connection when verdict curation is enabled.
        Note: DuckDB allows many read-only connections OR one writable
        connection — a writable handle excludes all others.
    """

    def __init__(self, db_path: str, read_only: bool = True):
        import duckdb  # deferred: keep import cost out of Postgres deployments

        self.db_path = db_path
        self.read_only = read_only
        self._conn = duckdb.connect(db_path, read_only=read_only)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        cursor = self._conn.execute(sql, list(params))
        columns = [d[0] for d in cursor.description]
        return rows_to_dicts(columns, cursor.fetchall())

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        self._conn.execute(sql, list(params))

    def close(self) -> None:
        self._conn.close()

    def __repr__(self) -> str:  # pragma: no cover - debugging nicety
        mode = "ro" if self.read_only else "rw"
        return f"DuckDBBackend({self.db_path!r}, {mode})"


class PostgresBackend:
    """Postgres engine via psycopg (v3) or psycopg2 — whichever is installed.

    Translates the query layer's ``?`` placeholders to ``%s``. The
    translation is safe because the query layer never embeds a literal
    ``?`` inside SQL strings (enforced by tests/test_backends.py).
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._conn = self._connect(dsn)
        self._conn.autocommit = True

    @staticmethod
    def _connect(dsn: str):
        try:
            import psycopg  # psycopg 3

            return psycopg.connect(dsn)
        except ImportError:
            try:
                import psycopg2

                return psycopg2.connect(dsn)
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    "PostgresBackend requires either 'psycopg[binary]' (v3) "
                    "or 'psycopg2-binary'. Install one: "
                    "pip install 'psycopg[binary]'"
                ) from exc

    @staticmethod
    def translate(sql: str) -> str:
        """``?`` → ``%s``. Query-layer SQL contains no literal ``?``."""
        return sql.replace("?", "%s")

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(self.translate(sql), list(params))
            columns = [d[0] for d in cur.description]
            return rows_to_dicts(columns, cur.fetchall())

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self._conn.cursor() as cur:
            cur.execute(self.translate(sql), list(params))

    def close(self) -> None:
        self._conn.close()

    def __repr__(self) -> str:  # pragma: no cover - debugging nicety
        return "PostgresBackend(<dsn redacted>)"
