#!/usr/bin/env python3
"""
Micro-adapter: receives OMP report_tool_issue grievance push payloads
and translates them into multica issues in the obsidian-library workspace.

Usage:
  ./scripts/multica-grievance-bridge.py [--port 9120] [--host 127.0.0.1]

OMP config (config.yml):
  dev:
    autoqa:
      consent: granted
    autoqaPush:
      endpoint: http://127.0.0.1:9120/push
      token: ""

Expected POST /push payload:
  {
    "agent": { "name": "omp", "version": "..." },
    "installId": "...",
    "platform": "linux",
    "arch": "x64",
    "entries": [
      { "id": 1, "model": "...", "version": "...", "tool": "...", "report": "..." }
    ]
  }

Each entry becomes a multica issue in the obsidian-library workspace.
"""
import argparse
import json
import subprocess
import sys
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ── config ──────────────────────────────────────────────────────────────────
MULTICA_PROFILE = "desktop-api.multica.ai"
MULTICA_WORKSPACE_ID = "a891648f-b3d2-4144-a13c-c58b720e3ca0"
DEFAULT_PORT = 9120
DEFAULT_HOST = "127.0.0.1"


def create_multica_issue(title: str, description: str, priority: str = "medium") -> dict | None:
    """Shell out to `multica issue create`. Returns parsed response or None."""
    cmd = [
        "multica",
        "--profile", MULTICA_PROFILE,
        "issue", "create",
        "--workspace-id", MULTICA_WORKSPACE_ID,
        "--title", title,
        "--priority", priority,
        "--description-stdin",
    ]
    try:
        result = subprocess.run(
            cmd,
            input=description.encode("utf-8"),
            capture_output=True,
            timeout=15,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            print(f"[bridge] multica error (rc={result.returncode}): {result.stderr.decode()[:200]}", file=sys.stderr)
            return None
    except subprocess.TimeoutExpired:
        print("[bridge] multica timed out", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[bridge] multica exception: {e}", file=sys.stderr)
        return None


class GrievanceHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for OMP grievance push."""

    def do_GET(self):
        try:
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"multica-grievance-bridge running\n")
        except Exception:
            self._crash("GET")

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            if path != "/push":
                self.send_response(404)
                self.end_headers()
                return

            length = int(self.headers.get("content-length", 0) or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as e:
                self._respond(400, {"ok": False, "error": f"invalid json: {e}"})
                return

            entries = payload.get("entries")
            if not isinstance(entries, list):
                self._respond(400, {"ok": False, "error": "'entries' must be a non-empty array", "got": type(entries).__name__})
                return

            if not entries:
                self._respond(200, {"ok": True, "created": 0, "note": "no entries"})
                return

            created = 0
            failures = 0

            for entry in entries:
                if not isinstance(entry, dict):
                    failures += 1
                    continue
                tool = entry.get("tool", "unknown")
                report = entry.get("report", "(no report)")
                model = entry.get("model", "unknown")

                report_preview = report[:120].replace("\n", " ").strip()
                title = f"auto-qa: {tool} — {report_preview}"
                if len(title) > 200:
                    title = title[:197] + "..."

                description = (
                    f"**Auto-QA Grievance**\n\n"
                    f"- **Tool:** {tool}\n"
                    f"- **Model:** {model}\n"
                    f"- **Version:** {entry.get('version', '?')}\n"
                    f"- **Agent:** OMP {payload.get('agent', {}).get('version', '?')}\n"
                    f"- **Install:** {payload.get('installId', '?')}\n"
                    f"- **Platform:** {payload.get('platform', '?')} / {payload.get('arch', '?')}\n\n"
                    f"**Report:**\n```\n{report}\n```\n\n"
                    f"_Discovered automatically via OMP report\\_tool\\_issue._"
                )

                issue = create_multica_issue(title, description)
                if issue:
                    created += 1
                    label = issue.get("identifier", issue.get("id", "?"))
                    print(f"[bridge] created {label}: {tool} — {report_preview[:60]}")
                else:
                    failures += 1

            self._respond(200, {"ok": True, "created": created, "failures": failures})

        except Exception as e:
            self._crash(f"POST: {e}")

    def _respond(self, status: int, data: dict):
        try:
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
        except Exception:
            pass  # client already disconnected

    def _crash(self, context: str):
        print(f"[bridge] UNHANDLED ERROR in {context}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        try:
            self._respond(500, {"ok": False, "error": "internal server error"})
        except Exception:
            pass

    def log_message(self, fmt, *args):
        print(f"[bridge] {args[0]} {args[1]} {args[2]}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="OMP grievance -> multica issue bridge")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind host (default {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"bind port (default {DEFAULT_PORT})")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), GrievanceHandler)
    print(f"[bridge] Listening on http://{args.host}:{args.port}/push", file=sys.stderr)
    print(f"[bridge] Multica profile: {MULTICA_PROFILE}", file=sys.stderr)
    print(f"[bridge] Workspace: {MULTICA_WORKSPACE_ID}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[bridge] Shutting down", file=sys.stderr)
        server.server_close()


if __name__ == "__main__":
    main()
