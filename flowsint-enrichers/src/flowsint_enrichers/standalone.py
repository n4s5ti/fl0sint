"""Standalone, source-preserving website scraping without graph or model services."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .website.text_extraction import WebsiteTextExtractor, WebsiteTextOccurrence

_package_name = __package__ or ""
_execution_package = _package_name.replace("_enrichers", "_execution")
_artifacts = importlib.import_module(f"{_execution_package}.artifacts")
_observed_extraction = importlib.import_module(
    f"{_execution_package}.observed_extraction"
)
_types_package = _package_name.replace("_enrichers", "_types")
Website = importlib.import_module(f"{_types_package}.website").Website
FilesystemArtifactStore = _artifacts.FilesystemArtifactStore
RetentionAuthority = _artifacts.RetentionAuthority
serialize_observed_extraction_metadata = (
    _observed_extraction.serialize_observed_extraction_metadata
)


SCHEMA_VERSION = "standalone-scraper/1.0"
EXIT_SUCCESS = 0
EXIT_PARTIAL = 2
EXIT_INVOCATION_FAILURE = 64


class InvocationError(ValueError):
    """Raised before an acquisition operation can be admitted."""


@dataclass(frozen=True)
class ScrapeOptions:
    """Validated bounds for a standalone scraper operation."""

    scope: str = "local-web-fetch"
    extraction: str = "observed-readable-text"
    max_concurrency: int = 10
    request_timeout: int = 10
    max_response_bytes: int = 1_048_576
    max_redirects: int = 3
    max_retries: int = 0

    def validate(self) -> None:
        if self.scope != "local-web-fetch":
            raise InvocationError("scope must be local-web-fetch")
        if self.extraction != "observed-readable-text":
            raise InvocationError("extraction must be observed-readable-text")
        for name in ("max_concurrency", "request_timeout", "max_response_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise InvocationError(f"{name} must be a positive integer")
        for name in ("max_redirects", "max_retries"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise InvocationError(f"{name} must be a non-negative integer")


@dataclass(frozen=True)
class ScrapeBundle:
    """A completed operation and the artifacts rendered from its one bundle."""

    payload: dict[str, Any]
    json_path: Path
    jsonl_path: Path
    report_path: Path

    @property
    def operation_id(self) -> str:
        return str(self.payload["operation_id"])

    @property
    def exit_code(self) -> int:
        statuses = {item["status"] for item in self.payload["outcomes"]}
        return EXIT_SUCCESS if statuses == {"success"} else EXIT_PARTIAL


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


def _normalize_inputs(inputs: object) -> tuple[tuple[str, str], ...]:
    if isinstance(inputs, str):
        items = (("input-0", inputs),)
    elif isinstance(inputs, Mapping):
        items = tuple(inputs.items())
    else:
        raise InvocationError("inputs must be one URL string or a keyed URL mapping")
    if not items:
        raise InvocationError("at least one input URL is required")

    normalized: list[tuple[str, str]] = []
    seen_keys: set[str] = set()
    for key, url in items:
        if not isinstance(key, str) or not key:
            raise InvocationError("each input key must be a non-empty string")
        if key in seen_keys:
            raise InvocationError(f"duplicate input key: {key}")
        if not isinstance(url, str):
            raise InvocationError(f"input {key} must be a URL string")
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise InvocationError(f"input {key} must be an absolute http(s) URL")
        seen_keys.add(key)
        normalized.append((key, url))
    return tuple(normalized)


def _authority(operation_id: str) -> RetentionAuthority:
    policy_digest = hashlib.sha256(
        f"{SCHEMA_VERSION}:{operation_id}".encode("utf-8")
    ).hexdigest()
    return RetentionAuthority(
        issuer_id="standalone-scraper",
        reviewer_id="standalone-scraper",
        policy_id="standalone-source-retention",
        policy_digest=policy_digest,
        caller_id="website-to-text",
        scope="local-web-fetch",
        source_family="http",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def _serialized_observations(
    occurrence: WebsiteTextOccurrence,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if occurrence.observation_result is None:
        return "", [], [], []
    metadata = serialize_observed_extraction_metadata(occurrence.observation_result)
    payload = _jsonable(metadata.payload)
    observations = list(payload.get("observations", []))
    links = [item for item in observations if item.get("kind") == "observed_link"]
    candidates = [item for item in observations if item.get("kind") != "observed_link"]
    for item in candidates:
        item["disposition"] = "unreviewed"
    return str(payload.get("readable_text", "")), observations, links, candidates


def _outcome_record(key: str, occurrence: WebsiteTextOccurrence) -> dict[str, Any]:
    readable_text, observations, links, candidates = _serialized_observations(occurrence)
    diagnostic = None
    if occurrence.diagnostic is not None:
        diagnostic = _jsonable(occurrence.diagnostic)
    source_reference = None
    if occurrence.source_proof is not None:
        source_reference = {
            "source_proof": occurrence.source_proof,
            "artifact": _jsonable(occurrence.artifact_reference),
            "spans": _jsonable(occurrence.span_references),
        }
    return {
        "key": key,
        "input_ref": occurrence.input_ref,
        "requested_url": str(occurrence.source.url),
        "status": occurrence.status.value,
        "text": [output.text for output in occurrence.outputs] or ([readable_text] if readable_text else []),
        "links": links,
        "candidates": candidates,
        "observations": observations,
        "disposition": "unreviewed",
        "source_reference": source_reference,
        "diagnostic": diagnostic,
        "resources": _jsonable(occurrence.actual_resources),
        "fetch_status": _jsonable(occurrence.fetch_status),
    }


def render_markdown_report(bundle: Mapping[str, Any]) -> str:
    """Render the readable report solely from a serialized machine bundle."""

    lines = [
        "# Standalone scraper report",
        "",
        f"- Schema: `{bundle['schema_version']}`",
        f"- Operation: `{bundle['operation_id']}`",
        f"- Scope: `{bundle['scope']}`",
        f"- Extraction: `{bundle['extraction']}`",
        "- Disposition: all links and candidates are **unreviewed observations**, not accepted contacts.",
    ]
    for outcome in bundle["outcomes"]:
        lines.extend(
            [
                "",
                f"## {outcome['key']} — {outcome['status']}",
                "",
                f"- Requested URL: `{outcome['requested_url']}`",
                f"- Input reference: `{outcome['input_ref']}`",
            ]
        )
        if outcome["diagnostic"] is not None:
            lines.append(
                f"- Error: `{outcome['diagnostic']['code']}` — {outcome['diagnostic']['safe_message']}"
            )
        lines.append("- Text:")
        lines.extend([f"  - {text}" for text in outcome["text"]] or ["  - _none_"])
        lines.append("- Observed links:")
        lines.extend([f"  - {item['value']}" for item in outcome["links"]] or ["  - _none_"])
        lines.append("- Typed candidates (unreviewed):")
        lines.extend(
            [f"  - {item['kind']}: {item['value']}" for item in outcome["candidates"]]
            or ["  - _none_"]
        )
        if outcome["source_reference"] is not None:
            artifact = outcome["source_reference"]["artifact"]
            lines.append(
                "- Source reference: "
                f"`{artifact['artifact_id']}` / `{artifact['content_digest']}`"
            )
        else:
            lines.append("- Source reference: _unavailable for this failed or held occurrence_")
    return "\n".join(lines) + "\n"


def _write_bundle(payload: dict[str, Any], output_dir: Path) -> ScrapeBundle:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "bundle.json"
    jsonl_path = output_dir / "outcomes.jsonl"
    report_path = output_dir / "report.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    jsonl_path.write_text(
        "".join(
            json.dumps(
                {
                    "schema_version": payload["schema_version"],
                    "operation_id": payload["operation_id"],
                    "outcome": outcome,
                },
                sort_keys=True,
            )
            + "\n"
            for outcome in payload["outcomes"]
        ),
        encoding="utf-8",
    )
    report_path.write_text(render_markdown_report(payload), encoding="utf-8")
    return ScrapeBundle(payload, json_path, jsonl_path, report_path)


async def scrape_websites(
    inputs: str | Mapping[str, str],
    *,
    output_dir: str | Path,
    options: ScrapeOptions | None = None,
) -> ScrapeBundle:
    """Acquire one URL or a keyed URL batch and persist matching evidence artifacts.

    Validation completes before this function creates or admits the underlying fetch operation.
    The function deliberately supplies no graph, model, planner, PostgreSQL, Redis, or Celery service.
    """

    resolved_options = options or ScrapeOptions()
    resolved_options.validate()
    normalized_inputs = _normalize_inputs(inputs)
    destination = Path(output_dir)
    operation_id = f"standalone-website-to-text-{uuid.uuid4().hex}"
    scanner = WebsiteTextExtractor(
        params={
            "max_concurrency": resolved_options.max_concurrency,
            "request_timeout": resolved_options.request_timeout,
            "max_response_bytes": resolved_options.max_response_bytes,
            "max_redirects": resolved_options.max_redirects,
            "max_retries": resolved_options.max_retries,
        },
        artifact_store=FilesystemArtifactStore(destination / "source-artifacts"),
        retention_authority=_authority(operation_id),
    )
    occurrences = await scanner.scan(
        [Website(url=url) for _, url in normalized_inputs], operation_id=operation_id
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "operation_id": operation_id,
        "scope": resolved_options.scope,
        "extraction": resolved_options.extraction,
        "options": _jsonable(resolved_options),
        "outcomes": [
            _outcome_record(key, occurrence)
            for (key, _), occurrence in zip(normalized_inputs, occurrences)
        ],
    }
    return _write_bundle(payload, destination)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="one absolute http(s) URL")
    source.add_argument("--batch", type=Path, help="JSON object mapping input keys to URLs")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scope", default="local-web-fetch")
    parser.add_argument("--extraction", default="observed-readable-text")
    parser.add_argument("--max-concurrency", type=int, default=10)
    parser.add_argument("--request-timeout", type=int, default=10)
    parser.add_argument("--max-response-bytes", type=int, default=1_048_576)
    parser.add_argument("--max-redirects", type=int, default=3)
    parser.add_argument("--max-retries", type=int, default=0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the thin CLI and return documented complete/partial/invocation status."""

    args = _parser().parse_args(argv)
    try:
        inputs: str | Mapping[str, str]
        if args.batch is None:
            inputs = args.url
        else:
            loaded = json.loads(args.batch.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise InvocationError("batch JSON must be an object mapping keys to URLs")
            inputs = loaded
        bundle = asyncio.run(
            scrape_websites(
                inputs,
                output_dir=args.output_dir,
                options=ScrapeOptions(
                    scope=args.scope,
                    extraction=args.extraction,
                    max_concurrency=args.max_concurrency,
                    request_timeout=args.request_timeout,
                    max_response_bytes=args.max_response_bytes,
                    max_redirects=args.max_redirects,
                    max_retries=args.max_retries,
                ),
            )
        )
    except (InvocationError, OSError, json.JSONDecodeError) as error:
        print(f"invocation failure: {error}", file=sys.stderr)
        return EXIT_INVOCATION_FAILURE
    print(
        json.dumps(
            {
                "operation_id": bundle.operation_id,
                "bundle": str(bundle.json_path),
                "jsonl": str(bundle.jsonl_path),
                "report": str(bundle.report_path),
                "exit_code": bundle.exit_code,
            },
            sort_keys=True,
        )
    )
    return bundle.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
