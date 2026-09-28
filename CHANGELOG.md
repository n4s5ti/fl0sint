# Changelog

All notable changes to Fl0sint will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- WebsiteToText now uses the shared admitted async HTTP fetch path with immutable caller/origin policy binding, finite shared request/byte/time/concurrency budgets, same-origin redirect checks, bounded retries, streaming body limits, verified TLS, and typed accounting-aware failures. The unsafe QUIC and synchronous requests fallback paths were removed.
- WebsiteToText now retains source-owned occurrence outcomes through asynchronous fetches, preserving duplicate, failed, cancelled, and one-to-many histories while creating HAS_INNER_TEXT edges from the correct Website.
- Structured execution distinguishes terminal transport failures from successful empty extraction and serializes the retained outcome groups. WebsiteToText scan now returns public WebsiteTextOccurrence envelopes; postprocess captures their graph ownership before adapting them to the legacy list[Phrase].
- Bounded fetch now converts streaming read failures to typed outcomes, retains per-occurrence accounting through deadlines and cancellation, records delivered overflow bytes, enforces WebsiteToText's response cap per input, and reconstructs source outcomes by occurrence ID.
