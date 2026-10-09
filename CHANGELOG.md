# Changelog

All notable changes to Fl0sint will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **DEF-48 (S08) P1 release smoke harness**: `scripts/release_smoke.py run|verify|suites`.
  - `run` packages the committed tree, installs it into a clean venv and drives the
    installed `*-standalone-scraper` against the loopback fixture corpus. It checks:
    - spans against the retained bytes;
    - lineage;
    - empty pages vs errors;
    - service and model isolation;
    - byte limits and SIGINT cancellation;
    - offline replay, plus negative controls.
    It also records package digests and resource use.
  - `suites` keeps the baseline failures listed in `docs/release/p1-baseline-failures.json`
    visible.
  - The P1 gate list, including the deferred browser, graph and learning gates, is in
    `docs/release/p1-release-checklist.md`.
- **DEF-90 (S09) audit-packet validator**: `scripts/audit_packet.py validate|changed`, a
  stdlib-only development checker for `docs/audits/<ISSUE>/packet.json`. It checks artifact
  digests, base/head/index freshness, pip3r semantic status (exit 0 is not treated as PASS),
  required cases, scope drift including untracked files, mutations, findings, and
  undeclared or self-approved fixture-label changes. The new **Audit packet validity** CI
  job runs it on changed packets and publishes the JSON report. Review acceptance stays
  outside the tool. See `docs/audit-packet.md`.
- Reviewed operation/caller/scope-bound local source retention, exact UTF-8 HTML raw-byte to
  normalized-character spans, deduplicated content with occurrence lineage, authorized
  resolution, explicit HOLD/REVIEW recovery, and shared WebsiteToText/connector capture.
- Deployment-owned artifact runtime configuration now supplies the current reviewed policy
  and trusted store to registry tasks, connector tasks, and template tests. Structured
  evidence retains a versioned resolvable proof envelope without bodies, secrets, or paths.

### Fixed
- Hardened DEF-44 source proof boundaries: trusted operation/occurrence resolution,
  whole-operation capture deadlines, compact exact HTML mappings, coordinated proof
  size limits, and valid empty example bundles.
- WebsiteToText now uses the shared admitted async HTTP fetch path with immutable caller/origin policy binding, finite shared request/byte/time/concurrency budgets, same-origin redirect checks, bounded retries, streaming body limits, verified TLS, and typed accounting-aware failures. The unsafe QUIC and synchronous requests fallback paths were removed.
- WebsiteToText now retains source-owned occurrence outcomes through asynchronous fetches, preserving duplicate, failed, cancelled, and one-to-many histories while creating HAS_INNER_TEXT edges from the correct Website.
- Structured execution distinguishes terminal transport failures from successful empty extraction and serializes the retained outcome groups. WebsiteToText scan now returns public WebsiteTextOccurrence envelopes; postprocess captures their graph ownership before adapting them to the legacy list[Phrase].
- Bounded fetch now converts streaming read failures to typed outcomes, retains per-occurrence accounting through deadlines and cancellation, records delivered overflow bytes, enforces WebsiteToText's response cap per input, and reconstructs source outcomes by occurrence ID.
- Bounded fetch now uses an independent deadline watcher and budgeted cleanup so cancellation-resistant transports cannot turn an expired operation into success or delay caller return indefinitely. Each admitted runtime object is atomically single-use, including its in-process model copies, so sequential or concurrent reuse cannot reset its allocation.
- WebsiteToText now rejects fetch responses unless the operation ID, exact unique occurrence IDs, cardinality, and input references match the admitted operation before any evidence is associated.
- **DEF-45 (S05) Observed Extraction**: Pure deterministic extraction of readable text,
  links, and contact observations from retained HTML with exact byte spans. New immutable
  observation models (ObservationKind, Observation, ObservedExtractionResult, RawSpan) in
  observed_extraction.py with separate versioning. Pure extractor function extract_observations()
  takes retained bytes only (no network/LLM/graph). Authorized bridge resolve_and_extract_observations()
  in extraction_runtime.py validates policy and invokes pure extractor.
  WebsiteToText attaches observations to occurrences post-capture without graph mutation; observations are
  metadata-only, never converted to Email/Phone/Website/Individual nodes. Observations carry
  unreviewed status always with explicit context spans for person/role associations. Links and
  external leads marked not_executable. Preserves S02 occurrence identity and S03/S04 bounds.
- Acquisition format 1.0 spans retain their original strict wire shape; normalized mappings
  use a separately versioned source-proof metadata contract.
- Finished DEF-45 observed-extraction integration: one authoritative pure model family,
  deadline-bounded live retained extraction, authorized saved-proof replay, exact raw
  span/digest provenance, and versioned metadata on WebsiteToText scan and structured
  results. Candidate links/forms remain unreviewed and non-executable.
- Hardened DEF-45 review findings: person attribution is captured at each candidate and
  bounded before its value; observation resolution proves canonical occurrence/input,
  snapshot, value, context, span, and extraction-policy ownership; hypothesis constructors
  enforce their invariants; and one conservative URL disclosure policy removes ambiguous
  credential queries from fetch and candidate URLs while preserving approved benign keys.
- Added strict `observed-extraction/1.0` metadata serialization/parsing, authorized persisted
  observation retrieval, and graph/auth-free `execute_live_observed_extraction()`. Standalone
  `--url --runtime-config` now uses it directly and reports actual resources and truthful
  HOLD/REVIEW/failure states without importing WebsiteToText.
