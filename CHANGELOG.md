# Changelog

All notable changes to Fl0sint will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- WebsiteToText now retains source-owned occurrence outcomes through asynchronous fetches, preserving duplicate, failed, cancelled, and one-to-many histories while creating HAS_INNER_TEXT edges from the correct Website.
- Structured execution distinguishes terminal transport failures from successful empty extraction and serializes the retained outcome groups. WebsiteToText scan now returns public WebsiteTextOccurrence envelopes; postprocess captures their graph ownership before adapting them to the legacy list[Phrase].
