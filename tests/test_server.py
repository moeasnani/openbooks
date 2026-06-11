"""HTTP server tests — routing, error containment, param validation.

Spin up the real server on an ephemeral port against the real warehouse.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from openbooks.server import make_handler

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def base_url(request):
    if not os.path.exists(os.path.join(REPO_ROOT, "warehouse.duckdb")):
        pytest.skip("warehouse.duckdb not present")
    from openbooks import OpenBooks

    ob = OpenBooks(os.path.join(REPO_ROOT, "warehouse.duckdb"))
    handler = make_handler(ob, cors=None, static_dir=REPO_ROOT)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{port}"

    httpd.shutdown()
    httpd.server_close()
    ob.close()


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_health(base_url):
    status, body = _get(f"{base_url}/api/health")
    assert status == 200 and body == {"status": "ok"}


def test_waterfall_endpoint(base_url):
    status, body = _get(f"{base_url}/api/waterfall")
    assert status == 200
    assert body["total_txns"] == 306_604


def test_entity_endpoint(base_url):
    status, body = _get(f"{base_url}/api/entity?q=FONDOMONTE")
    assert status == 200
    assert "FONDOMONTE" in body["entity_key"]


def test_entity_no_match_is_404(base_url):
    status, body = _get(f"{base_url}/api/entity?q=ZZZNOSUCHVENDOR999")
    assert status == 404
    assert "error" in body


def test_unknown_route_is_404(base_url):
    status, body = _get(f"{base_url}/api/nope")
    assert status == 404


def test_bad_int_param_is_400_not_500(base_url):
    status, body = _get(f"{base_url}/api/leads?tier=banana")
    assert status == 400
    assert "tier" in body["error"]


def test_limit_is_clamped(base_url):
    status, body = _get(f"{base_url}/api/leads?tier=1&limit=999999")
    assert status == 200
    assert len(body) <= 500


def test_post_invalid_json_is_400(base_url):
    req = urllib.request.Request(
        f"{base_url}/api/verdict",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 400


def test_post_verdict_on_readonly_is_500_with_json_error(base_url):
    """Valid verdict on a read-only server: contained JSON error, not a
    dead socket (the original server.py would have crashed the handler)."""
    payload = json.dumps({"entity_key": "X", "verdict": "genuine_review"}).encode()
    req = urllib.request.Request(
        f"{base_url}/api/verdict",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status, body = resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        status, body = e.code, json.loads(e.read())
    assert status == 500
    assert "error" in body


def test_index_served(base_url):
    with urllib.request.urlopen(base_url, timeout=10) as resp:
        assert resp.status == 200
        assert b"OpenBooks" in resp.read()
