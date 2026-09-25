"""Contract tests for the DEF-40 synthetic acquisition fixture corpus.

The corpus labels are authored truth; these tests guard the loader, the
fixture server and the label invariants so that neither can drift to match
future scraper output. No models, graph services or internet access are used.
"""

import copy
import hashlib
import importlib.util
import json
import re
import shutil
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "flowsint-enrichers/tests/fixtures/acquisition"
_spec = importlib.util.spec_from_file_location(
    "acquisition_fixtures", REPO_ROOT / "scripts/acquisition_fixtures.py"
)
fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixtures)


@pytest.fixture(scope="module")
def corpus():
    return fixtures.load_manifest(FIXTURE_DIR / "manifest.json")


def _by_id(rows):
    return {row["id"]: row for row in rows}


def test_accepted_labels_are_grounded_in_retained_source_spans(corpus):
    manifest, bodies = corpus
    observations = _by_id(manifest["observations"])
    accepted = [
        label
        for case in manifest["cases"]
        for label in case["acceptance"]
        if label["verdict"] == "accepted"
    ]
    assert accepted
    for label in accepted:
        evidence = [observations[oid] for oid in label["evidence_observation_ids"]]
        for observation in evidence:
            raw = bodies[observation["source_id"]]
            span = raw[observation["start_byte"] : observation["end_byte"]]
            assert span == observation["quote"].encode("utf-8")
        quoted = " ".join(observation["quote"] for observation in evidence)
        for field in ("subject", "company", "value"):
            assert label[field] in quoted, (label, field)


# Canonical negative labels for the trap fixtures. Span grounding cannot stop a
# verdict flip when every label word also occurs in the quoted evidence.
TRAP_VERDICTS = {
    (
        "same-name-different-companies",
        "Ari Vale",
        "Amber Loom",
        "published_work_contact",
        "ari.vale@cobalt-kite.example",
    ): "rejected",
    (
        "generic-inbox-not-person",
        "Néra Vale",
        "Lumé Lantern",
        "published_work_contact",
        "hello@lume-lantern.example",
    ): "rejected",
    (
        "copied-pages-one-origin",
        "Néra Vale",
        "Lumé Lantern",
        "independent_contact_evidence_origins",
        "2",
    ): "rejected",
    (
        "duplicate-url-occurrences",
        "Néra Vale",
        "Lumé Lantern",
        "independent_contact_evidence_origins",
        "2",
    ): "rejected",
    (
        "stale-role-not-current",
        "Sora Pell",
        "Cobalt Kite",
        "current_role",
        "operations lead",
    ): "unresolved",
    (
        "stale-role-not-current",
        "Sora Pell",
        "Cobalt Kite",
        "current_work_contact",
        "sora.pell@cobalt-kite.example",
    ): "unresolved",
}


def test_trap_fixtures_keep_their_negative_verdicts(corpus):
    manifest, _ = corpus
    verdicts = {
        (
            case["id"],
            label["subject"],
            label["company"],
            label["predicate"],
            label["value"],
        ): label["verdict"]
        for case in manifest["cases"]
        for label in case["acceptance"]
    }
    for key, verdict in TRAP_VERDICTS.items():
        assert verdicts.get(key) == verdict, key


def test_duplicate_url_inputs_keep_distinct_occurrences_but_share_content(corpus):
    manifest, bodies = corpus
    sources = _by_id(manifest["sources"])
    repeated = [o for o in manifest["occurrences"] if o["source_id"] == "named"]
    assert len(repeated) == 2
    assert len({o["id"] for o in repeated}) == 2
    assert len({o["input_index"] for o in repeated}) == 2
    assert len({sources[o["source_id"]]["route"] for o in repeated}) == 1

    copies = [sources[s] for s in ("named", "copy-a", "copy-b")]
    assert len({s["route"] for s in copies}) == 3
    assert len({s["origin_id"] for s in copies}) == 1
    assert len({hashlib.sha256(bodies[s["id"]]).hexdigest() for s in copies}) == 1


def test_empty_success_and_execution_failures_are_distinct_labels(corpus):
    manifest, _ = corpus
    sources = _by_id(manifest["sources"])
    occurrences = _by_id(manifest["occurrences"])
    by_status = {}
    for case in manifest["cases"]:
        for entry in case["expected"]:
            by_status.setdefault(entry["status"], []).append(entry)
    for status in ("success_empty", "failure_http", "failure_timeout"):
        assert by_status.get(status), status
    for entry in by_status["success_empty"]:
        response = sources[occurrences[entry["occurrence_id"]]["source_id"]]["response"]
        assert response["status"] == 200 and entry["diagnostic"] is None
        assert entry["observation_ids"] == []
    for entry in by_status["failure_http"] + by_status["failure_timeout"]:
        assert entry["diagnostic"] and entry["observation_ids"] == []


def test_smoke_exercises_real_http_and_matches_every_expected_label(corpus):
    manifest, bodies = corpus
    report = fixtures.smoke(manifest, bodies)
    assert report["passed"] is True
    expected = {
        (case["id"], entry["occurrence_id"]): (case["capability"], entry["status"])
        for case in manifest["cases"]
        for entry in case["expected"]
    }
    observed = {(r["case_id"], r["occurrence_id"]): r for r in report["results"]}
    assert observed.keys() == expected.keys()
    for key, (capability, status) in expected.items():
        result = observed[key]
        if capability == "http":
            assert result["status"] == status and result["passed"] is True, result
        else:
            assert result["status"] == "NOT_RUN_BROWSER" and result["passed"] is None


def test_server_serves_only_manifest_routes(corpus):
    manifest, bodies = corpus
    opener = build_opener(ProxyHandler({}))
    with fixtures.fixture_server(manifest, bodies) as base_url:
        with opener.open(base_url + "/static", timeout=2) as response:
            assert response.read() == bodies["static"]
        for path in ("/manifest.json", "/static.html", "/../manifest.json", "/"):
            with pytest.raises(HTTPError) as error:
                opener.open(base_url + path, timeout=2)
            assert error.value.code == 404, path


def _mutation_helpers(manifest):
    def by_id(key, row_id):
        return next(row for row in manifest[key] if row["id"] == row_id)

    def expected(case_id, occurrence_id):
        case = by_id("cases", case_id)
        return next(e for e in case["expected"] if e["occurrence_id"] == occurrence_id)

    return by_id, expected


MUTATIONS = {
    "source hash mismatch": lambda m, by_id, exp, d: (d / "static.html").write_bytes(
        (d / "static.html").read_bytes() + b" "
    ),
    "byte span mismatch": lambda m, by_id, exp, d: by_id(
        "observations", "named-contact"
    ).update(start_byte=155, end_byte=264),
    "duplicate id": lambda m, by_id, exp, d: m["observations"].append(
        copy.deepcopy(by_id("observations", "static-text"))
    ),
    "dangling reference": lambda m, by_id, exp, d: by_id(
        "cases", "legitimate-empty-page"
    )["occurrence_ids"].append("input-missing"),
    "invalid artifact filename": lambda m, by_id, exp, d: by_id(
        "sources", "static"
    ).update(artifact="../acquisition/static.html"),
    "invalid route": lambda m, by_id, exp, d: by_id("sources", "static").update(
        route="/../static"
    ),
    "redirect outside corpus": lambda m, by_id, exp, d: by_id("sources", "redirect")[
        "response"
    ].update(location="/elsewhere"),
    "cross-occurrence evidence attribution": lambda m, by_id, exp, d: exp(
        "same-name-different-companies", "input-same-name-cobalt"
    )["observation_ids"].append("amber-contact"),
    "acceptance evidence outside case occurrences": lambda m, by_id, exp, d: by_id(
        "cases", "named-person-company-contact"
    )["acceptance"][0]["evidence_observation_ids"].append("cobalt-contact"),
    "empty/nonempty label mismatch": lambda m, by_id, exp, d: exp(
        "legitimate-empty-page", "input-empty"
    ).update(status="success_nonempty"),
    "success/failure label mismatch": lambda m, by_id, exp, d: exp(
        "http-503-failure", "input-failure"
    ).update(status="success_empty", diagnostic=None),
    "HTTP failure label mismatch": lambda m, by_id, exp, d: exp(
        "legitimate-empty-page", "input-empty"
    ).update(status="failure_http", diagnostic="HTTP 503"),
    "timeout label mismatch": lambda m, by_id, exp, d: exp(
        "slow-page-timeout", "input-slow"
    ).update(timeout_ms=2000),
    "duplicate or invalid input index": lambda m, by_id, exp, d: by_id(
        "occurrences", "input-named-repeat"
    ).update(input_index=1),
    "extracted value not in quoted span": lambda m, by_id, exp, d: by_id(
        "observations", "static-link"
    ).update(value="/elsewhere"),
    "retention policy hash mismatch": lambda m, by_id, exp, d: m["retention"].update(
        policy=m["retention"]["policy"] + " Also retain live data."
    ),
    "dynamic observations must be script literals": lambda m, by_id, exp, d: by_id(
        "observations", "dynamic-contact"
    ).update(kind="contact"),
    "orphan observation": lambda m, by_id, exp, d: m["observations"].append(
        dict(by_id("observations", "static-text"), id="static-text-uncited")
    ),
}


@pytest.mark.parametrize("message", sorted(MUTATIONS))
def test_loader_rejects_tampered_corpus(tmp_path, message):
    corpus_dir = tmp_path / "acquisition"
    shutil.copytree(FIXTURE_DIR, corpus_dir)
    manifest_path = corpus_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_id, expected = _mutation_helpers(manifest)
    MUTATIONS[message](manifest, by_id, expected, corpus_dir)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=re.escape(message)):
        fixtures.load_manifest(manifest_path)
