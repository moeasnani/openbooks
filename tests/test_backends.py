"""Backend seam tests — placeholder translation and JSON-safety."""

from __future__ import annotations

import datetime
import decimal
import re

import openbooks.bootstrap as bootstrap_mod
import openbooks.queries as queries_mod
from openbooks.db import PostgresBackend, rows_to_dicts


def test_placeholder_translation():
    assert PostgresBackend.translate("SELECT * FROM t WHERE a = ? AND b = ?") == (
        "SELECT * FROM t WHERE a = %s AND b = %s"
    )


def test_percent_literals_escaped_for_psycopg():
    """LIKE-pattern percents must become %% so psycopg doesn't read them
    as placeholders (found live against Postgres 16)."""
    assert PostgresBackend.translate("WHERE x LIKE '%' || ? || '%'") == (
        "WHERE x LIKE '%%' || %s || '%%'"
    )


def test_no_question_mark_inside_sql_string_literals():
    """The blanket `?`→`%s` translation in PostgresBackend is only safe if
    no SQL emitted by the query layer carries a literal question mark
    inside a quoted string. Scan the source of both SQL-emitting modules:
    within every single-quoted span of every line, no `?` allowed."""
    for mod in (queries_mod, bootstrap_mod):
        assert mod.__file__ is not None
        with open(mod.__file__) as f:
            for lineno, line in enumerate(f, 1):
                for span in re.findall(r"'([^']*)'", line):
                    assert "?" not in span, (
                        f"{mod.__name__}:{lineno} has '?' inside a string "
                        f"literal — would be corrupted by Postgres translation: {line!r}"
                    )


def test_rows_to_dicts_json_safety():
    rows = [
        (
            decimal.Decimal("12.50"),
            datetime.date(2024, 6, 30),
            datetime.datetime(2024, 6, 30, 12, 0),
            "text",
            None,
            7,
        )
    ]
    out = rows_to_dicts(["dec", "d", "dt", "s", "none", "i"], rows)[0]
    assert out["dec"] == 12.5 and isinstance(out["dec"], float)
    assert out["d"] == "2024-06-30"
    assert out["dt"].startswith("2024-06-30T12:00")
    assert out["s"] == "text"
    assert out["none"] is None
    assert out["i"] == 7
