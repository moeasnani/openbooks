#!/usr/bin/env python3
"""Backward-compatibility shim — the implementation moved to the
``openbooks`` package. Prefer::

    from openbooks import OpenBooks          # library use
    python -m openbooks entity FONDOMONTE    # CLI use

This shim keeps ``from query import OpenBooks`` and
``python query.py <command>`` working for existing callers.

Note one intentional behavior change inherited from the package: the
connection is READ-ONLY by default. For verdict curation construct
``OpenBooks(db_path, writable=True)``.
"""

import sys

from openbooks import OpenBooks  # noqa: F401  (re-export)

if __name__ == "__main__":
    from openbooks.__main__ import main

    # Map old positional style (query.py entity NAME) onto the new CLI.
    sys.exit(main(sys.argv[1:]))
