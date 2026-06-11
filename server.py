#!/usr/bin/env python3
"""
OpenBooks API Server — thin HTTP wrapper for query.py.
Start: python3 server.py
Then open http://localhost:8765 in a browser.
"""
import json, sys, os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from query import OpenBooks

ob = OpenBooks(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'warehouse.duckdb'))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        params = parse_qs(parsed.query)

        if path in ('', '/index.html'):
            return self._static('index.html')

        routes = {
            '/api/entity':    lambda: ob.entity(params.get('q', [''])[0]),
            '/api/leads':     lambda: ob.leads(tier=int(params.get('tier',[1])[0]), status=params.get('status',[None])[0], agency=params.get('agency',[None])[0], limit=int(params.get('limit',[50])[0])),
            '/api/agency':    lambda: ob.agency_card(params.get('q', [''])[0]),
            '/api/explain':   lambda: ob.explain(params.get('q', [''])[0]),
            '/api/search':    lambda: ob.search(params.get('q', [''])[0], limit=20),
            '/api/waterfall': lambda: ob.waterfall(),
            '/api/pending':   lambda: ob.verdicts_pending(limit=int(params.get('limit',[50])[0])),
        }
        fn = routes.get(path)
        if fn: return self._json(fn())
        self._err(404, 'not found')

    def do_POST(self):
        length = int(self.headers.get('content-length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        if self.path.rstrip('/') == '/api/verdict':
            return self._json(ob.set_verdict(body.get('entity_key',''), body.get('verdict',''), overtaker_interest=body.get('overtaker_interest',0), public_context=body.get('public_context',''), recommended_action=body.get('recommended_action','')))
        self._err(404, 'not found')

    def _json(self, data):
        body = json.dumps(data, default=str).encode()
        self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Access-Control-Allow-Origin','*'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)

    def _static(self, fn):
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), fn)
        if not os.path.exists(p): return self._err(404, 'not found')
        with open(p,'rb') as f: c = f.read()
        self.send_response(200); self.send_header('Content-Type','text/html'); self.send_header('Content-Length',str(len(c))); self.end_headers(); self.wfile.write(c)

    def _err(self, code, msg):
        self.send_response(code); self.end_headers(); self.wfile.write(json.dumps({'error':msg}).encode())

    def log_message(self, *a): pass

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f'\n  OpenBooks → http://localhost:{port}\n')
    HTTPServer(('127.0.0.1', port), Handler).serve_forever()
