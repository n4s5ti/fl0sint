#!/usr/bin/env python3
"""Development fixture integrity/transport checks, not a scraper or fact evaluator."""

import argparse
import hashlib
import json
import re
import signal
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "flowsint-enrichers/tests/fixtures/acquisition/manifest.json"
)
MAX_BODY = 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def index_rows(rows, label):
    require(isinstance(rows, list), f"{label}: expected list")
    result = {}
    for row in rows:
        require(isinstance(row, dict), f"{label}: expected object")
        key = row.get("id")
        require(isinstance(key, str) and key, f"{label}: missing id")
        require(key not in result, f"{label}: duplicate id {key}")
        result[key] = row
    return result


def references(values, index, label):
    require(isinstance(values, list), f"{label}: expected list")
    require(all(isinstance(v, str) for v in values), f"{label}: invalid id")
    require(len(values) == len(set(values)), f"{label}: duplicate reference")
    require(all(v in index for v in values), f"{label}: dangling reference")


def timestamp(value, label):
    require(isinstance(value, str), f"{label}: expected UTC ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label}: invalid timestamp") from exc
    require(parsed.utcoffset() == timezone.utc.utcoffset(parsed), f"{label}: not UTC")


def route_valid(value):
    return isinstance(value, str) and re.fullmatch(r"/[A-Za-z0-9_/-]+(?:\.html)?", value) and "//" not in value


def load_manifest(path=DEFAULT_MANIFEST):
    """Load retained bytes and validate references without rewriting any labels/hashes."""
    path = Path(path).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(manifest, dict), "manifest: expected object")
    require(type(manifest.get("version")) is int and manifest["version"] == 1, "unsupported version")
    require(manifest.get("corpus_id") == "def40-acquisition-v1", "wrong corpus id")
    require(manifest.get("development_only") is True, "corpus must be development-only")
    timestamp(manifest.get("as_of"), "as_of")
    provenance = manifest.get("provenance", {})
    require(isinstance(provenance, dict), "provenance: expected object")
    require(provenance.get("synthetic") is True and provenance.get("no_live_persons") is True, "synthetic provenance required")
    for field in ("authoring", "reviewer_requirement"):
        require(isinstance(provenance.get(field), str) and provenance[field].strip(), f"provenance: missing {field}")
    retention = manifest.get("retention", {})
    require(isinstance(retention, dict), "retention: expected object")
    require(retention.get("permission") == "synthetic_author_permission", "retention permission required")
    policy = retention.get("policy")
    require(isinstance(policy, str) and policy.strip(), "retention policy required")
    require(hashlib.sha256(policy.encode("utf-8")).hexdigest() == retention.get("sha256"), "retention policy hash mismatch")

    sources = index_rows(manifest.get("sources"), "sources")
    observations = index_rows(manifest.get("observations"), "observations")
    occurrences = index_rows(manifest.get("occurrences"), "occurrences")
    cases = index_rows(manifest.get("cases"), "cases")
    require(0 < len(sources) <= 128, "source count outside fixture bounds")
    require(cases and occurrences, "cases and occurrences required")
    routes, bodies, origins = {}, {}, {}
    for source_id, source in sources.items():
        route = source.get("route")
        require(route_valid(route), f"{source_id}: invalid route")
        require(route not in routes, f"{source_id}: duplicate route")
        routes[route] = source_id
        artifact = source.get("artifact")
        require(isinstance(artifact, str) and re.fullmatch(r"[A-Za-z0-9_-]+\.html", artifact), f"{source_id}: invalid artifact filename")
        artifact_path = path.parent / artifact
        require(artifact_path.resolve().parent == path.parent, f"{source_id}: artifact traversal")
        require(artifact_path.stat().st_size <= MAX_BODY, f"{source_id}: artifact too large")
        body = artifact_path.read_bytes()
        body.decode("utf-8")
        require(hashlib.sha256(body).hexdigest() == source.get("sha256"), f"{source_id}: source hash mismatch")
        bodies[source_id] = body
        origin = source.get("origin_id")
        require(isinstance(origin, str) and origin, f"{source_id}: missing origin")
        if origin in origins:
            require(origins[origin] == source["sha256"], f"{source_id}: copied origin differs")
        origins[origin] = source["sha256"]
        timestamp(source.get("event_at"), f"{source_id}.event_at")
        response = source.get("response")
        require(isinstance(response, dict) and set(response) <= {"status", "delay_ms", "location"}, f"{source_id}: unsupported response shape")
        require(type(response.get("status")) is int and response["status"] in (200, 302, 503), f"{source_id}: unsupported HTTP status")
        delay = response.get("delay_ms")
        require(type(delay) is int and 0 <= delay <= 5000, f"{source_id}: invalid delay")
        if response["status"] == 302:
            require(route_valid(response.get("location")), f"{source_id}: invalid redirect")
        else:
            require(response.get("location") is None, f"{source_id}: unexpected redirect")
    for source in sources.values():
        if source["response"]["status"] == 302:
            require(source["response"]["location"] in routes, "redirect outside corpus")

    def final_source(source_id):
        visited = set()
        while sources[source_id]["response"]["status"] == 302:
            require(source_id not in visited, "redirect cycle")
            visited.add(source_id)
            source_id = routes[sources[source_id]["response"]["location"]]
        return source_id

    for source_id in sources:
        final_source(source_id)
    for observation_id, observation in observations.items():
        source_id = observation.get("source_id")
        require(source_id in sources, f"{observation_id}: dangling source")
        start, end = observation.get("start_byte"), observation.get("end_byte")
        require(type(start) is int and type(end) is int and 0 <= start < end <= len(bodies[source_id]), f"{observation_id}: invalid byte span")
        quote = observation.get("quote")
        require(isinstance(quote, str) and quote.encode("utf-8") == bodies[source_id][start:end], f"{observation_id}: byte span mismatch")
        kind = observation.get("kind")
        require(kind in ("text", "link", "contact", "script_literal"), f"{observation_id}: unsupported kind")
        require(isinstance(observation.get("value"), str) and observation["value"], f"{observation_id}: missing value")
        if kind in ("link", "contact", "script_literal"):
            require(observation["value"] in quote, f"{observation_id}: extracted value not in quoted span")
    input_indexes = set()
    for occurrence_id, occurrence in occurrences.items():
        require(occurrence.get("source_id") in sources, f"{occurrence_id}: dangling source")
        input_index = occurrence.get("input_index")
        require(type(input_index) is int and input_index >= 0 and input_index not in input_indexes, f"{occurrence_id}: duplicate or invalid input index")
        input_indexes.add(input_index)
    cited = set()
    for case_id, case in cases.items():
        capability = case.get("capability")
        require(capability in ("http", "render", "interaction"), f"{case_id}: invalid capability")
        references(case.get("occurrence_ids"), occurrences, case_id)
        require(case["occurrence_ids"], f"{case_id}: no occurrences")
        expected = case.get("expected")
        require(isinstance(expected, list), f"{case_id}: expected must be list")
        expected_ids = [entry.get("occurrence_id") for entry in expected]
        require(len(expected_ids) == len(set(expected_ids)) and set(expected_ids) == set(case["occurrence_ids"]), f"{case_id}: expected occurrence mismatch")
        case_evidence = set()
        for entry in expected:
            occurrence_id = entry["occurrence_id"]
            source_id = final_source(occurrences[occurrence_id]["source_id"])
            require(entry.get("final_source_id") == source_id, f"{case_id}: wrong final source")
            references(entry.get("observation_ids"), observations, occurrence_id)
            require(all(observations[oid]["source_id"] == source_id for oid in entry["observation_ids"]), f"{occurrence_id}: cross-occurrence evidence attribution")
            timeout = entry.get("timeout_ms")
            require(type(timeout) is int and 0 < timeout <= 5000, f"{occurrence_id}: invalid timeout")
            status = entry.get("status")
            require(status in ("success_nonempty", "success_empty", "failure_http", "failure_timeout"), f"{occurrence_id}: invalid status")
            response = sources[source_id]["response"]
            if status.startswith("failure_"):
                require(not entry["observation_ids"] and not any(o["source_id"] == source_id for o in observations.values()), f"{occurrence_id}: failure has observations")
                require(isinstance(entry.get("diagnostic"), str) and entry["diagnostic"].strip(), f"{occurrence_id}: missing failure diagnostic")
                if status == "failure_http":
                    require(response["status"] == 503 and response["delay_ms"] < timeout, f"{occurrence_id}: HTTP failure label mismatch")
                else:
                    require(response["delay_ms"] >= timeout, f"{occurrence_id}: timeout label mismatch")
            else:
                require(response["status"] == 200 and response["delay_ms"] < timeout, f"{occurrence_id}: success/failure label mismatch")
                require(entry.get("diagnostic") is None, f"{occurrence_id}: success has diagnostic")
                require(bool(entry["observation_ids"]) == (status == "success_nonempty"), f"{occurrence_id}: empty/nonempty label mismatch")
                if status == "success_empty":
                    require(not any(o["source_id"] == source_id for o in observations.values()), f"{occurrence_id}: empty source has observations")
            case_evidence.update(entry["observation_ids"])
        cited.update(case_evidence)
        acceptance = case.get("acceptance")
        require(isinstance(acceptance, list), f"{case_id}: acceptance must be list")
        for label in acceptance:
            for field in ("subject", "company", "predicate", "value", "reason"):
                require(isinstance(label.get(field), str) and label[field].strip(), f"{case_id}: missing acceptance {field}")
            require(label.get("verdict") in ("accepted", "rejected", "unresolved"), f"{case_id}: invalid verdict")
            references(label.get("evidence_observation_ids"), observations, case_id)
            require(set(label["evidence_observation_ids"]) <= case_evidence, f"{case_id}: acceptance evidence outside case occurrences")
        browser = case.get("browser")
        if capability == "http":
            require(browser is None, f"{case_id}: HTTP case has browser requirements")
        else:
            require(isinstance(browser, dict), f"{case_id}: browser proof required")
            for field in ("selector", "before_text", "after_text"):
                require(isinstance(browser.get(field), str), f"{case_id}: invalid browser {field}")
            require(bool(browser["selector"]) and bool(browser["after_text"]), f"{case_id}: missing browser target")
            if capability == "interaction":
                require(isinstance(browser.get("action_selector"), str) and browser["action_selector"], f"{case_id}: missing browser action")
            else:
                require(browser.get("action_selector") is None, f"{case_id}: unexpected browser action")
            require(all(observations[oid]["kind"] == "script_literal" for oid in case_evidence), f"{case_id}: dynamic observations must be script literals")
    require(cited == set(observations), f"orphan observation: {sorted(set(observations) - cited)}")
    return manifest, bodies


@contextmanager
def fixture_server(manifest, bodies, port=0):
    """Serve only retained routes, with bounded delays, bodies and concurrent requests."""
    routes = {source["route"]: source for source in manifest["sources"]}
    stopping = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            source = routes.get(self.path)
            if source is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            response = source["response"]
            if stopping.wait(response["delay_ms"] / 1000):
                return
            body = bodies[source["id"]]
            try:
                self.send_response(response["status"])
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                if response["status"] == 302:
                    self.send_header("Location", response["location"])
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Expected when the timeout fixture's client disconnects.

        def log_message(self, format, *args):
            pass

    class Server(ThreadingHTTPServer):
        daemon_threads = False

        def __init__(self):
            self.slots = threading.BoundedSemaphore(16)
            super().__init__(("127.0.0.1", port), Handler)

        def process_request(self, request, client_address):
            request.settimeout(5)
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self.slots.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.slots.release()

    server = Server()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        stopping.set()
        server.shutdown()
        server.server_close()
        thread.join()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def smoke(manifest, bodies):
    """Exercise real HTTP; observation presence is not independently extracted truth."""
    sources = {s["id"]: s for s in manifest["sources"]}
    routes = {s["route"]: s["id"] for s in sources.values()}
    occurrences = {o["id"]: o for o in manifest["occurrences"]}
    observations = {o["id"]: o for o in manifest["observations"]}
    opener = build_opener(ProxyHandler({}), NoRedirect())
    results = []
    with fixture_server(manifest, bodies) as base_url:
        for case in manifest["cases"]:
            for expected in case["expected"]:
                occurrence_id = expected["occurrence_id"]
                occurrence = occurrences[occurrence_id]
                result = {"case_id": case["id"], "occurrence_id": occurrence_id, "input_index": occurrence["input_index"], "source_id": occurrence["source_id"]}
                if case["capability"] != "http":
                    result.update(status="NOT_RUN_BROWSER", passed=None)
                    results.append(result)
                    continue
                current_id = occurrence["source_id"]
                hops, verified, errors = [], [], []
                deadline = time.monotonic() + expected["timeout_ms"] / 1000
                status, diagnostic = None, None
                for _ in range(len(sources) + 1):
                    source = sources[current_id]
                    try:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("fixture deadline exceeded")
                        try:
                            response = opener.open(base_url + source["route"], timeout=remaining)
                        except HTTPError as exc:
                            response = exc
                        with response:
                            body = response.read(MAX_BODY + 1)
                            http_status = response.code
                            location = response.headers.get("Location")
                        hops.append({"source_id": current_id, "http_status": http_status})
                        if hashlib.sha256(body).hexdigest() != source["sha256"]:
                            errors.append("fetched body hash mismatch")
                        if http_status != source["response"]["status"]:
                            errors.append("HTTP status mismatch")
                        if http_status == 302:
                            if location != source["response"]["location"] or location not in routes:
                                errors.append("redirect location mismatch")
                                status = "failure_http"
                                break
                            current_id = routes[location]
                            continue
                        if http_status != 200:
                            status, diagnostic = "failure_http", f"HTTP {http_status}"
                        else:
                            for oid in expected["observation_ids"]:
                                observation = observations[oid]
                                if observation["source_id"] == current_id and body[observation["start_byte"]:observation["end_byte"]] == observation["quote"].encode("utf-8"):
                                    verified.append(oid)
                                else:
                                    errors.append(f"fetched span mismatch: {oid}")
                            status = "success_nonempty" if verified else "success_empty"
                        break
                    except (TimeoutError, URLError) as exc:
                        if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError):
                            status, diagnostic = "failure_timeout", "HTTP request timed out"
                        else:
                            status, diagnostic = "failure_http", str(exc)
                        break
                else:
                    errors.append("redirect limit exceeded")
                if status != expected["status"]:
                    errors.append("acquisition label mismatch")
                if current_id != expected["final_source_id"]:
                    errors.append("final source mismatch")
                if verified != expected["observation_ids"]:
                    errors.append("verified observations mismatch")
                result.update(status=status, final_source_id=current_id, observation_ids=verified, diagnostic=diagnostic, hops=hops, errors=errors, passed=not errors)
                results.append(result)
    not_run = [r["case_id"] for r in results if r["passed"] is None]
    return {"corpus_id": manifest["corpus_id"], "development_only": True, "claim": "Fixture HTTP transport and retained-byte integrity only; labels are authored, not extracted or factually evaluated. Browser cases are not run; passed covers HTTP cases only.", "passed": all(r["passed"] is not False for r in results), "not_run_browser_cases": not_run, "results": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "smoke", "serve"))
    parser.add_argument("--port", type=int, default=0, help="serve: loopback port (default: OS-assigned)")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535 or (args.command != "serve" and args.port != 0):
        parser.error("--port must be 0..65535 and is only for serve")
    try:
        manifest, bodies = load_manifest()
        if args.command == "validate":
            result = {"valid": True, "corpus_id": manifest["corpus_id"], "development_only": True, "sources": len(bodies), "observations": len(manifest["observations"]), "occurrences": len(manifest["occurrences"]), "cases": len(manifest["cases"])}
        elif args.command == "smoke":
            result = smoke(manifest, bodies)
        else:
            def stop(signum, frame):
                raise KeyboardInterrupt

            previous = signal.signal(signal.SIGTERM, stop)
            try:
                with fixture_server(manifest, bodies, args.port) as base_url:
                    print(json.dumps({"base_url": base_url, "corpus_id": manifest["corpus_id"], "development_only": True}), flush=True)
                    try:
                        threading.Event().wait()
                    except KeyboardInterrupt:
                        pass
            finally:
                signal.signal(signal.SIGTERM, previous)
            return 0
        print(json.dumps(result), flush=True)
        return 0 if result.get("passed", True) else 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
