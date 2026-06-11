"""OpenBooks — analytics query layer over state checkbook spending data.

Public API:
    from openbooks import OpenBooks
    ob = OpenBooks("warehouse.duckdb")              # DuckDB (embedded)
    ob = OpenBooks.from_postgres("postgresql://…")  # Postgres (server)
"""

from openbooks._sql import normalize_entity_key
from openbooks.db import DuckDBBackend, PostgresBackend
from openbooks.queries import OpenBooks

__version__ = "1.0.0"
__all__ = ["OpenBooks", "DuckDBBackend", "PostgresBackend", "normalize_entity_key"]
