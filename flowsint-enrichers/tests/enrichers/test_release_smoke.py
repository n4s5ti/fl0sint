"""DEF-48 release smoke harness (scripts/release_smoke.py) verification behaviour.

Bundles come from the real standalone scraper run against the DEF-40 loopback
fixture corpus; each test then breaks one property and expects the offline
verifier to reject it.
"""

import asyncio
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from flowsint_enrichers.standalone import render_markdown_report, scrape_websites

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load("release_smoke")
fixtures = _load("acquisition_fixtures")


@pytest.fixture(scope="module")
def produced(tmp_path_factory):
    """One real mixed-batch bundle (plus the fixture base URL) shared read-only."""
    manifest, bodies = fixtures.load_manifest()
    out = tmp_path_factory.mktemp("bundle") / "batch"
    with fixtures.fixture_server(manifest, bodies) as base_url:
        routes = {s["id"]: base_url + s["route"] for s in manifest["sources"]}
        batch = {key: routes[source] for key, (source, _, _) in smoke.BATCH.items()}
        asyncio.run(scrape_websites(batch, output_dir=out))
    return out, manifest, base_url


@pytest.fixture
def bundle_dir(produced, tmp_path):
    copy = tmp_path / "copy"
    shutil.copytree(produced[0], copy)
    return copy


def _rewrite(directory, mutate):
    bundle = json.loads((directory / "bundle.json").read_text())
    mutate(bundle)
    (directory / "bundle.json").write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
    (directory / "outcomes.jsonl").write_text("".join(
        json.dumps({"schema_version": bundle["schema_version"], "operation_id": bundle["operation_id"],
                    "outcome": o}, sort_keys=True) + "\n"
        for o in bundle["outcomes"]
    ))
    (directory / "report.md").write_text(render_markdown_report(bundle))


def _outcome(bundle, key):
    return next(o for o in bundle["outcomes"] if o["key"] == key)


def _codes(problems):
    return {p.split(":", 1)[0] for p in problems}


def test_real_bundle_verifies_offline_and_matches_fixture_expectations(produced):
    out, manifest, base_url = produced
    assert smoke.verify_bundle(out, render_markdown_report) == []
    assert smoke.check_expectations(out, manifest, base_url) == []


def test_broken_observation_lineage_fails(bundle_dir):
    def mutate(bundle):
        _outcome(bundle, "static")["links"][0]["occurrence_id"] = "input-6"
    _rewrite(bundle_dir, mutate)
    assert "E_LINEAGE" in _codes(smoke.verify_bundle(bundle_dir))


def test_swapped_outcome_order_breaks_proof_lineage(bundle_dir):
    def mutate(bundle):
        bundle["outcomes"][0], bundle["outcomes"][1] = bundle["outcomes"][1], bundle["outcomes"][0]
    _rewrite(bundle_dir, mutate)
    assert "E_LINEAGE" in _codes(smoke.verify_bundle(bundle_dir))


def test_proof_context_must_match_the_outcome(bundle_dir):
    # Observations stay consistent; only the outcome's requested URL disagrees with its proof.
    _rewrite(bundle_dir, lambda bundle: _outcome(bundle, "named").update(requested_url="http://127.0.0.1:9/elsewhere"))
    problems = smoke.verify_bundle(bundle_dir)
    assert any(p.startswith("E_LINEAGE") and "proof context requested_url" in p for p in problems)


def _all_items(outcome):
    return outcome["links"] + outcome["candidates"] + outcome["observations"]


def test_forged_candidate_value_fails(bundle_dir):
    def mutate(bundle):
        for item in _all_items(_outcome(bundle, "named")):
            item["value"] = "ceo-forged@evil.example"
    _rewrite(bundle_dir, mutate)
    assert "E_SPAN" in _codes(smoke.verify_bundle(bundle_dir, render_markdown_report))


def test_fabricated_text_fails_even_with_consistent_report(bundle_dir):
    _rewrite(bundle_dir, lambda bundle: _outcome(bundle, "static").update(text=["FABRICATED claim"]))
    assert "E_SPAN" in _codes(smoke.verify_bundle(bundle_dir, render_markdown_report))


def test_link_rebased_onto_another_host_fails(bundle_dir):
    def mutate(bundle):
        for item in _all_items(_outcome(bundle, "static")):
            item["source_url"] = "http://evil.example/"
            item["value"] = "http://evil.example/named"
    _rewrite(bundle_dir, mutate)
    assert "E_LINEAGE" in _codes(smoke.verify_bundle(bundle_dir, render_markdown_report))


def test_unrecorded_candidate_fails(bundle_dir):
    def mutate(bundle):
        outcome = _outcome(bundle, "named")
        outcome["observations"] = []
    _rewrite(bundle_dir, mutate)
    assert "E_LINEAGE" in _codes(smoke.verify_bundle(bundle_dir))


def test_failure_cannot_smuggle_a_source_reference(bundle_dir):
    def mutate(bundle):
        _outcome(bundle, "failure")["source_reference"] = _outcome(bundle, "static")["source_reference"]
    _rewrite(bundle_dir, mutate)
    assert "E_ERROR_EMPTY" in _codes(smoke.verify_bundle(bundle_dir))


@pytest.mark.parametrize("digest", ["/etc/passwd", "../../outside", "ZZ" * 32])
def test_path_shaped_digest_is_rejected_without_reading_it(bundle_dir, digest):
    def mutate(bundle):
        _outcome(bundle, "static")["source_reference"]["artifact"]["content_digest"] = digest
    _rewrite(bundle_dir, mutate)
    assert "E_SOURCE_DIGEST" in _codes(smoke.verify_bundle(bundle_dir))


def test_malformed_record_span_is_reported_not_raised(bundle_dir):
    snapshot = _outcome(json.loads((bundle_dir / "bundle.json").read_text()), "static")["source_reference"]["artifact"]["snapshot_id"]
    record_path = bundle_dir / "source-artifacts/records" / f"{snapshot}.json"
    record = json.loads(record_path.read_text())
    del record["normalized"]["spans"][0]["raw_start"]
    record_path.write_text(json.dumps(record))
    assert "E_SPAN" in _codes(smoke.verify_bundle(bundle_dir))


def test_missing_source_proof_on_success_fails(bundle_dir):
    _rewrite(bundle_dir, lambda bundle: _outcome(bundle, "named").update(source_reference=None))
    assert "E_SOURCE_PROOF_MISSING" in _codes(smoke.verify_bundle(bundle_dir))


def test_missing_or_altered_retained_body_fails(bundle_dir):
    digest = json.loads((bundle_dir / "bundle.json").read_text())["outcomes"][1]["source_reference"]["artifact"]["content_digest"]
    body = bundle_dir / "source-artifacts/objects" / digest[:2] / f"{digest}.body"
    body.write_bytes(body.read_bytes().replace(b"<", b"[", 1))
    assert "E_SOURCE_DIGEST" in _codes(smoke.verify_bundle(bundle_dir))
    body.unlink()
    assert "E_SOURCE_PROOF_MISSING" in _codes(smoke.verify_bundle(bundle_dir))


def test_span_text_not_in_retained_bytes_fails(bundle_dir):
    def mutate(bundle):
        _outcome(bundle, "static")["links"][0]["raw_span"]["start_byte"] -= 3
    _rewrite(bundle_dir, mutate)
    assert "E_SPAN" in _codes(smoke.verify_bundle(bundle_dir))


def test_error_and_empty_success_stay_distinct(bundle_dir, produced):
    _, manifest, base_url = produced

    def error_as_empty(bundle):
        _outcome(bundle, "failure").update(diagnostic=None)
    _rewrite(bundle_dir, error_as_empty)
    assert "E_ERROR_EMPTY" in _codes(smoke.verify_bundle(bundle_dir))

    def empty_as_error(bundle):
        _outcome(bundle, "failure").update(diagnostic={"code": "http_error", "retryable": False, "safe_message": "x"})
        _outcome(bundle, "empty").update(status="failure", source_reference=None,
                                         diagnostic={"code": "http_error", "retryable": False, "safe_message": "x"})
    _rewrite(bundle_dir, empty_as_error)
    assert smoke.verify_bundle(bundle_dir) == []  # internally coherent, so only the labels catch it
    assert "E_OUTCOME" in _codes(smoke.check_expectations(bundle_dir, manifest, base_url))


def test_jsonl_and_report_must_agree_with_bundle(bundle_dir):
    jsonl = bundle_dir / "outcomes.jsonl"
    jsonl.write_text("".join(jsonl.read_text().splitlines(keepends=True)[:-1]))
    assert "E_JSONL" in _codes(smoke.verify_bundle(bundle_dir))
    report = bundle_dir / "report.md"
    # Headings intact; only an extracted value was edited, which only a re-render detects.
    report.write_text(report.read_text().replace("/named", "/forged", 1))
    assert smoke.verify_bundle(bundle_dir) == [p for p in smoke.verify_bundle(bundle_dir) if p.startswith("E_JSONL")]
    assert "E_REPORT" in _codes(smoke.verify_bundle(bundle_dir, render_markdown_report))


def test_retained_bytes_must_be_the_fixture_bytes(bundle_dir, produced):
    _, manifest, base_url = produced
    altered = json.loads(json.dumps(manifest))
    next(s for s in altered["sources"] if s["id"] == "named")["sha256"] = "0" * 64
    assert "E_SOURCE_DIGEST" in _codes(smoke.check_expectations(bundle_dir, altered, base_url))


def test_audit_rejects_service_imports_external_connects_and_silence():
    port = 41000
    ok = [{"event": "import", "module": "json"}, {"event": "socket.connect", "address": ["127.0.0.1", port]}]
    assert smoke.audit_problems(ok, port) == []
    assert smoke.audit_problems(ok + [{"event": "import", "module": "neo4j"}], port)
    assert smoke.audit_problems(ok + [{"event": "socket.connect", "address": ["10.0.0.1", 7687]}], port)
    assert smoke.audit_problems([], port)  # no hook output means isolation is unproven


def test_baseline_failures_stay_visible_and_new_failures_fail():
    baseline = {"core": ["tests/a.py::known", "tests/a.py::fixed"]}
    result = smoke.compare_baseline({"core": ["tests/a.py::known", "tests/b.py::new"], "api": []}, baseline)
    assert result["core"] == {
        "new_failures": ["tests/b.py::new"],
        "baseline_debt": ["tests/a.py::known"],
        "baseline_resolved": ["tests/a.py::fixed"],
        "status": "FAIL",
    }
    assert result["api"]["status"] == "PASS"
    known_only = smoke.compare_baseline({"core": ["tests/a.py::known"]}, baseline)["core"]
    assert known_only["status"] == "PASS" and known_only["baseline_debt"] == ["tests/a.py::known"]


def test_pytest_failure_lines_are_parsed():
    output = (
        "ERROR    celery.app.trace:trace.py:309 Task emit_event raised unexpected\n"
        "ERROR:root:Failed to batch insert logs\n"
        "FAILED tests/x.py::t1 - AssertionError: boom\nERROR tests/y.py - KeyError\n1 failed\n"
    )
    assert smoke.parse_failures(output) == ["tests/x.py::t1", "tests/y.py"]
