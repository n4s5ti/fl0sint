#!/usr/bin/env python3
"""Instrumented DEF-40 fixture server: counts requests/bytes/concurrency to JSONL."""
import importlib.util, json, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(sys.argv[1]); LOG = Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("acquisition_fixtures", REPO / "scripts/acquisition_fixtures.py")
fx = importlib.util.module_from_spec(spec); spec.loader.exec_module(fx)

_lock = threading.Lock(); _active = 0; _peak = 0
def log(rec):
    with _lock:
        with open(LOG, "a", encoding="utf-8") as h: h.write(json.dumps(rec) + "\n")

class CountingBase(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        orig, this = self.wfile, self
        class W:
            def write(s, b):
                this._bytes_out = getattr(this, "_bytes_out", 0) + len(b)
                return orig.write(b)
            def flush(s): return orig.flush()
            def close(s): return orig.close()
        self.wfile = W()
    def parse_request(self):
        ok = super().parse_request()
        if ok:
            log({"t": time.monotonic(), "event": "request", "path": self.path})
        return ok
    def handle(self):
        global _active, _peak
        with _lock:
            _active += 1; _peak = max(_peak, _active)
        try:
            super().handle()
        finally:
            with _lock: _active -= 1
            log({"t": time.monotonic(), "event": "connection_closed",
                 "bytes_out": getattr(self, "_bytes_out", 0), "peak_concurrency": _peak})

fx.BaseHTTPRequestHandler = CountingBase
manifest, bodies = fx.load_manifest()
with fx.fixture_server(manifest, bodies) as base_url:
    print(json.dumps({"base_url": base_url}), flush=True)
    threading.Event().wait()
