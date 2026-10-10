#!/usr/bin/env python3
"""TA12 loopback adversarial servers: self-signed TLS + cross-origin/chain/oversize redirects."""
import json, ssl, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FIXTURE = sys.argv[1]  # e.g. http://127.0.0.1:34583

class TLSHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><body>tls page secret@tls.example</body></html>"
        self.send_response(200); self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass

class RedHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/out":
            self.send_response(302); self.send_header("Location", FIXTURE + "/named")
            self.send_header("Content-Length", "0"); self.end_headers(); return
        if self.path.startswith("/chain/"):
            n = int(self.path.rsplit("/",1)[1])
            self.send_response(302); self.send_header("Location", f"/chain/{n+1}")
            self.send_header("Content-Length", "0"); self.end_headers(); return
        if self.path == "/big-cl":
            body = b"A" * (2 * 1024 * 1024)
            self.send_response(200); self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "text/html"); self.end_headers()
            try: self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass
            return
        if self.path == "/big-stream":
            self.send_response(200); self.send_header("Content-Type", "text/html")
            self.end_headers()  # no Content-Length: body bounded only by stream
            chunk = b"B" * 65536
            try:
                for _ in range(32): self.wfile.write(chunk)  # 2 MiB
            except (BrokenPipeError, ConnectionResetError): pass
            return
        self.send_response(404); self.send_header("Content-Length","0"); self.end_headers()
    def log_message(self, *a): pass

tls = ThreadingHTTPServer(("127.0.0.1", 0), TLSHandler)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain("cert.pem", "key.pem")
tls.socket = ctx.wrap_socket(tls.socket, server_side=True)
red = ThreadingHTTPServer(("127.0.0.1", 0), RedHandler)
print(json.dumps({"tls_port": tls.server_address[1], "red_port": red.server_address[1]}), flush=True)
threading.Thread(target=tls.serve_forever, daemon=True).start()
red.serve_forever()
