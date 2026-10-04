"""DEF-47 acceptance coverage for the standalone scraper function and CLI."""

import importlib
import json
import os
import pkgutil
import subprocess
import sys
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

_package = next(module.name for module in pkgutil.iter_modules() if module.name.endswith("enrichers"))
_standalone = importlib.import_module(f"{_package}.standalone")
EXIT_INVOCATION_FAILURE = _standalone.EXIT_INVOCATION_FAILURE
EXIT_PARTIAL = _standalone.EXIT_PARTIAL
InvocationError = _standalone.InvocationError
main = _standalone.main
scrape_websites = _standalone.scrape_websites


@contextmanager
def _fixture_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/failure":
                self.send_response(500)
                self.end_headers()
                return
            body = (
                b"<html><body><a href='/next'>next</a>"
                b"<p>info@example.test</p></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_standalone_import_requires_no_legacy_service_environment():
    source_root = Path(__file__).parents[2] / "src"
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "PYTHONPATH": str(source_root),
    }

    completed = subprocess.run(
        [sys.executable, "-c", f"import {_package}.standalone"],
        cwd=source_root.parent,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.asyncio
async def test_direct_function_preserves_mixed_results_and_source_references(tmp_path):
    """A second caller can use the library without shell or graph/runtime services."""
    with _fixture_server() as base_url:
        bundle = await scrape_websites(
            {"good": f"{base_url}/success", "bad": f"{base_url}/failure"},
            output_dir=tmp_path,
        )

    assert bundle.exit_code == EXIT_PARTIAL
    good, bad = bundle.payload["outcomes"]
    assert [good["status"], bad["status"]] == ["success", "failure"]
    assert good["text"]
    assert good["links"][0]["value"] == f"{base_url}/next"
    assert good["source_reference"]["artifact"]["requested_url"] == f"{base_url}/success"
    assert bad["diagnostic"]["code"] == "http_error"
    assert bad["source_reference"] is None


@pytest.mark.asyncio
async def test_report_json_and_jsonl_agree_on_every_outcome(tmp_path):
    with _fixture_server() as base_url:
        bundle = await scrape_websites({"only": f"{base_url}/success"}, output_dir=tmp_path)

    machine = json.loads(bundle.json_path.read_text(encoding="utf-8"))
    jsonl = [json.loads(line) for line in bundle.jsonl_path.read_text(encoding="utf-8").splitlines()]
    report = bundle.report_path.read_text(encoding="utf-8")
    assert machine == bundle.payload
    assert [line["outcome"] for line in jsonl] == machine["outcomes"]
    assert all(line["operation_id"] == machine["operation_id"] for line in jsonl)
    assert machine["operation_id"] in report
    assert machine["outcomes"][0]["input_ref"] in report
    assert machine["outcomes"][0]["links"][0]["value"] in report


@pytest.mark.asyncio
async def test_invalid_input_fails_before_output_or_admission(tmp_path):
    with pytest.raises(InvocationError, match="absolute http"):
        await scrape_websites({"invalid": "ftp://example.test"}, output_dir=tmp_path)
    assert not any(tmp_path.iterdir())


def test_cli_returns_invocation_failure_without_writing_output(tmp_path, capsys):
    exit_code = main(["--url", "ftp://example.test", "--output-dir", str(tmp_path)])

    assert exit_code == EXIT_INVOCATION_FAILURE
    assert "invocation failure" in capsys.readouterr().err
    assert not any(tmp_path.iterdir())


def test_cli_runs_thin_layer_over_function(tmp_path, capsys):
    with _fixture_server() as base_url:
        exit_code = main(
            [
                "--url",
                f"{base_url}/success",
                "--output-dir",
                str(tmp_path),
            ]
        )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["exit_code"] == 0
    assert Path(output["bundle"]).is_file()
