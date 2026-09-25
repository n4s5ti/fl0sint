"""Stub HTTP API server for FlowSint MCP CLI E2E testing.

Uses Python 3 stdlib only (http.server). Defaults to port 15001.
"""

import os
from pathlib import Path
import ast
import json
import signal
import socketserver
import sys
from http.server import BaseHTTPRequestHandler
from typing import Any

STUB_PORT = int(os.environ.get("FLOWSINT_STUB_PORT", "15001"))
REPO_ROOT = Path(__file__).resolve().parents[2]
ENRICHERS_DIR = REPO_ROOT / "flowsint-enrichers" / "src" / "flowsint_enrichers"


def _has_enricher_decorator(node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "flowsint_enricher":
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr == "flowsint_enricher":
            return True
        if isinstance(decorator, ast.Call):
            func = decorator.func
            if isinstance(func, ast.Name) and func.id == "flowsint_enricher":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "flowsint_enricher":
                return True
    return False


def _extract_enricher_name(node: ast.ClassDef) -> str | None:
    for item in node.body:
        if not isinstance(item, ast.FunctionDef) or item.name != "name":
            continue
        for stmt in item.body:
            if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant):
                value = stmt.value.value
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _load_enricher_names() -> list[str]:
    if not ENRICHERS_DIR.exists():
        # Fallback used when run from a pared-down environment.
        return [
            "domain_to_whois",
            "ip_to_asn",
            "website_to_scrapling",
        ]

    names: list[str] = []
    for path in sorted(ENRICHERS_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue

        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if not _has_enricher_decorator(node):
                continue
            name = _extract_enricher_name(node)
            if name is None:
                name = node.name
            if name not in names:
                names.append(name)

    return sorted(names)


KNOWN_ENRICHERS = _load_enricher_names()


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    """Threaded TCP server with SO_REUSEADDR set before bind."""
    allow_reuse_address = True
    daemon_threads = True


class StubHandler(BaseHTTPRequestHandler):
    """Minimal stub that responds to known endpoints with canned JSON."""
    REQUEST_LOG: list[dict[str, Any]] = []

    def _record_request(self, method: str, body: object | None) -> None:
        self.REQUEST_LOG.append({
            "method": method,
            "path": self.path,
            "body": body,
        })

    @classmethod
    def _requests_for_client(cls) -> list[dict[str, Any]]:
        return cls.REQUEST_LOG

    def _send_json(self, status: int, body):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            return self._send_json(200, {"status": "ok"})
        if self.path == "/__requests":
            return self._send_json(200, self._requests_for_client())
        if self.path == "/api/enrichers":
            return self._send_json(
                200,
                [
                    {"name": name, "category": "auto"}
                    for name in KNOWN_ENRICHERS
                ],
            )
        if self.path == "/api/investigations":
            return self._send_json(200, [{"id": "inv-1", "title": "Test Investigation"}])
        if self.path == "/api/enrichers/templates":
            return self._send_json(
                200, [{"id": "tmpl-1", "name": "test-template", "category": "domain"}]
            )
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw_body = self.rfile.read(length) if length else b""
        body: object | None = None
        if raw_body:
            body_text = raw_body.decode("utf-8", errors="replace")
            try:
                body = json.loads(body_text)
            except json.JSONDecodeError:
                body = body_text
        self._record_request("POST", body)

        if self.path == "/api/auth/token":
            return self._send_json(200, {"access_token": "fake-jwt-token"})
        if self.path == "/api/enrichers/templates":
            body_dict = body if isinstance(body, dict) else {}
            content = body_dict.get("content", {})
            rendered_name = ""
            if isinstance(content, dict):
                rendered_name = content.get("name", "")
            rendered_category = ""
            if isinstance(content, dict):
                rendered_category = content.get("category", "")
            return self._send_json(
                201,
                {
                    "id": "tmpl-2",
                    "name": "cli-created-template",
                    "category": "domain",
                    "is_public": False,
                    "rendered_name": rendered_name,
                    "rendered_category": rendered_category,
                    "rendered_content": content,
                    "content": content,
                },
            )
        if self.path.startswith("/api/enrichers/") and self.path.endswith("/launch"):
            enricher = self.path.split("/")[3]
            return self._send_json(
                200,
                {"id": f"job-{enricher}", "status": "queued"},
            )
        return self._send_json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        """Quiet logging — no per-request noise to stdout."""
        pass


def main():
    server = ReusableTCPServer(("", STUB_PORT), StubHandler)

    def _sigterm(signum, frame):
        server.shutdown()

    signal.signal(signal.SIGTERM, _sigterm)
    sys.stdout.write("STUB_READY\n")
    sys.stdout.flush()
    server.serve_forever()


if __name__ == "__main__":
    main()
