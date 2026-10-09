#!/usr/bin/env python3
"""Independent DEF-45 adversarial reproducer. Does not modify the archive."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ARCHIVE = Path("/home/n4s5ti/.cache/def45-independent")
PYTHON = Path("/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python")
SOURCE_PATHS = [
    ARCHIVE / "flowsint-core/src",
    ARCHIVE / "flowsint-enrichers/src",
    ARCHIVE / "flowsint-types/src",
    ARCHIVE / "flowsint-mcp-server/src",
    ARCHIVE / "flowsint-app/src",
]
EXPECTED = {
    "flowsint-core/src/flowsint_execution/observed_extraction.py": "dfba3437848215ee15eb0446b5bdfc7eaf869c33e5b45f56e4f81c17a3434fd0",
    "flowsint-core/src/flowsint_execution/extraction_runtime.py": "de9ebe74a7c12ebb0f9904203de04d68685673aafb8c348d99e3e4845ea6973c",
    "flowsint-core/src/flowsint_execution/fetch.py": "1101aa4abdb8e5614127ed3b7e674dfd5a4aaa7aad27cdd1d89e9c796e60ba11",
    "flowsint-core/examples/observed_extraction.py": "013d0b68e918f61896ab2a106d92f25db5a742d45a8e5de097722d1f3b622d2a",
    "flowsint-enrichers/src/flowsint_enrichers/website/to_text.py": "27ad027e22e4462d204a7cdcbe97609b6ab2ce5c64a52b1bf965ce174528f718",
    "flowsint-core/src/flowsint_execution/models.py": "a493d3a19fb2b4575131dc3d22f9befe93083609ed8c6ff83160bbba22f4be42",
    "flowsint-core/src/flowsint_execution/artifact_runtime.py": "37e9257b56fc0f6eac06faae51d131aa2b9508bccab243a41c489ac3ec758fa1",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(label: str, condition: bool, detail: object) -> None:
    print(f"{'PASS' if condition else 'FAIL'} {label}: {detail}")
    if not condition:
        raise AssertionError(label)


for relative, expected in EXPECTED.items():
    actual = sha(ARCHIVE / relative)
    check("archive hash " + relative, actual == expected, actual)

sys.path[:0] = [str(path) for path in SOURCE_PATHS]
os.environ.setdefault("AUTH_SECRET", "def45-fixture-only")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379")

from flowsint_execution.acquisition import ArtifactReference
from flowsint_execution.fetch import _redacted_location
from flowsint_execution.observed_extraction import (
    GeneratedHypothesis,
    ObservationKind,
    RawSpan,
    extract_observations,
    resolve_observation_span,
)


def artifact(body: bytes, final_url: str = "https://example.test/team/index.html") -> ArtifactReference:
    digest = hashlib.sha256(body).hexdigest()
    return ArtifactReference(
        artifact_id="artifact-review", snapshot_id="snapshot-review",
        content_digest=digest, byte_length=len(body), locator="sha256:" + digest,
        source_family="http", origin="https://example.test:443",
        retrieved_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
        requested_url=final_url, final_url=final_url,
    )


def extract(body: bytes):
    ref = artifact(body)
    return extract_observations(
        body, artifact=ref, occurrence_id="occ-review", input_ref="a" * 64,
        final_url=ref.final_url,
    )


# F1: a later heading retroactively names an earlier address in the same card.
body = b'<section class="person"><p>first@example.test</p><h2>Later Person</h2></section>'
obs = next(item for item in extract(body).observations if item.value == "first@example.test")
check("F1 reproduced later-heading identity", obs.person_name == "Later Person", (obs.kind.value, obs.person_name))

# F1 also concatenates multiple people in one explicit parent and applies both to all contacts.
body = (b'<section class="person"><h2>Ada One</h2><p>ada@example.test</p>'
        b'<h2>Bo Two</h2><p>bo@example.test</p></section>')
people = {item.value: item.person_name for item in extract(body).observations if item.person_name}
check("F1 reproduced multi-person conflation",
      people == {"ada@example.test": "Ada One Bo Two", "bo@example.test": "Ada One Bo Two"}, people)

# F2: the public resolver authenticates the artifact but accepts any in-bounds range;
# observation_kind is ignored and no occurrence/snapshot/value ownership is supplied.
body = b"alpha@example.test beta@example.test"
ref = artifact(body)
foreign_span = RawSpan(body.index(b"beta"), body.index(b"beta") + len(b"beta@example.test"))
raw, text = resolve_observation_span(body, ref, foreign_span, ObservationKind.GENERAL_INBOX)
check("F2 reproduced unowned span resolution", text == "beta@example.test", raw)

# F3: GeneratedHypothesis.create validates, but its public constructor bypasses all checks.
hypothesis = GeneratedHypothesis("", "", "", ())
check("F3 reproduced hypothesis constructor bypass", hypothesis.source_observation_ids == (), hypothesis)

# F4: S04 URL redaction leaks common credential aliases while preserving benign query.
leaks = {
    key: _redacted_location(f"https://example.test/x?{key}=fixture-secret&view=full")
    for key in ("client_secret", "auth_token", "passwd", "pwd")
}
check("F4 reproduced credential alias leakage",
      all("fixture-secret" in value for value in leaks.values()), leaks)
encoded = _redacted_location("https://example.test/x?api%5fkey=fixture-secret")
benign = _redacted_location("https://example.test/x?view=full&page=2")
check("F4 controls: percent-encoded canonical key redacted", "fixture-secret" not in encoded, encoded)
check("F4 controls: meaningful query retained", benign.endswith("?view=full&page=2"), benign)

# F5: standalone --url imports the graph/auth stack and fails before admission when
# AUTH_SECRET is absent. A closed loopback port avoids external network if regression is fixed.
env = os.environ.copy()
env.pop("AUTH_SECRET", None)
env.pop("FLOWSINT_ARTIFACT_RUNTIME_CONFIG", None)
env["PYTHONPATH"] = os.pathsep.join(str(path) for path in SOURCE_PATHS)
run = subprocess.run(
    [str(PYTHON), str(ARCHIVE / "flowsint-core/examples/observed_extraction.py"),
     "--url", "https://127.0.0.1:1/"],
    env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10,
)
check("F5 reproduced standalone auth prerequisite leak",
      run.returncode != 0 and "AUTH_SECRET environment variable is not set" in run.stdout,
      run.stdout.splitlines()[-1] if run.stdout.splitlines() else run.returncode)

# F6: --runtime-config is consumed only by saved replay. The live function has no
# parameter for it, and WebsiteToText loads only environment/default configuration.
example_source = (ARCHIVE / "flowsint-core/examples/observed_extraction.py").read_text()
to_text_source = (ARCHIVE / "flowsint-enrichers/src/flowsint_enrichers/website/to_text.py").read_text()
check("F6 reproduced live runtime-config disconnect",
      "async def _live(url: str)" in example_source
      and "config_path=" not in example_source.split("async def _live", 1)[1].split("async def _saved", 1)[0]
      and 'source_family="http"\n                )' in to_text_source,
      "_live accepts only URL; loader has no config_path")

# F7: the integrated observation payload is not a versioned validated model.
# It is declared object/Any, converted to an ordinary dict, and stored in Any metadata.
models_source = (ARCHIVE / "flowsint-core/src/flowsint_execution/models.py").read_text()
check("F7 reproduced ad-hoc observation metadata contract",
      "observation_result: object | None" in to_text_source
      and "**asdict(self.observation_result)" in to_text_source
      and "metadata: tuple[Any, ...] = ()" in models_source,
      "object -> asdict -> tuple[Any, ...]")

print("REPRO COMPLETE: seven defects reproduced; no archive files were written.")
