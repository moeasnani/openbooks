"""Shared fixtures. Tests run against the real warehouse.duckdb (read-only),
skipping cleanly when it isn't present (e.g. CI without the data drop)."""

from __future__ import annotations

import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAREHOUSE = os.path.join(REPO_ROOT, "warehouse.duckdb")

requires_warehouse = pytest.mark.skipif(
    not os.path.exists(WAREHOUSE),
    reason="warehouse.duckdb not present (data artifacts are not in git)",
)


@pytest.fixture(scope="session")
def ob():
    """A read-only OpenBooks instance over the real warehouse."""
    if not os.path.exists(WAREHOUSE):
        pytest.skip("warehouse.duckdb not present")
    from openbooks import OpenBooks

    instance = OpenBooks(WAREHOUSE)
    yield instance
    instance.close()
