#!/usr/bin/env python3
"""Backward-compatibility shim — the server moved to ``openbooks.server``.

Prefer: ``python -m openbooks.server [--port N]``.
This shim keeps ``python3 server.py [port]`` working as before.
"""

import sys

from openbooks.server import main

if __name__ == "__main__":
    # Old invocation style: `python3 server.py 9000`
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        sys.argv = [sys.argv[0], "--port", sys.argv[1]]
    main()
