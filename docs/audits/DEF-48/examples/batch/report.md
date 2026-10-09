# Standalone scraper report

- Schema: `standalone-scraper/1.0`
- Operation: `standalone-website-to-text-f3cfdee2866a4954aa03988cc3c3fd2e`
- Scope: `local-web-fetch`
- Extraction: `observed-readable-text`
- Disposition: all links and candidates are **unreviewed observations**, not accepted contacts.

## static — success

- Requested URL: `http://127.0.0.1:37071/static`
- Input reference: `196c32e045357876e45368079bec17e538c312ef7ef38881e602f97599c9fa67`
- Text:
  - Lumé Lantern archive Lumé Lantern Lumé Lantern publishes café lighting notes — résumé available. Lumé Lantern contact directory
- Observed links:
  - http://127.0.0.1:37071/named
- Typed candidates (unreviewed):
  - _none_
- Source reference: `artifact-7d3b6144f11825643e77ff2332153fc8` / `5e33eded4a2b9619420b7f39d49239d77889ab9b8727cc2ccc0bfb05b43c86fa`

## named — success

- Requested URL: `http://127.0.0.1:37071/named`
- Input reference: `979ff729e2fa861c489d4afeaa5b395aa0ccb9e018ee1123818a7824de646725`
- Text:
  - Lumé Lantern contact Lumé Lantern Néra Vale is the design lead at Lumé Lantern. Her published work contact is nera.vale@lume-lantern.example. Directory effective 2026-09-01. All people and companies on this page are fictional.
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - observed_unknown_contact: nera.vale@lume-lantern.example
- Source reference: `artifact-4520e94cd310976d8e3faf43932f0d5f` / `d75f55711a996c3f878fa4f6b0f95037ae74e19237a844fe12ae22573162161a`

## malformed — success

- Requested URL: `http://127.0.0.1:37071/malformed`
- Input reference: `31124a597163550b6a57d2f1764bd1a03e74566538295e4c2d1591a046cffed4`
- Text:
  - Amber Loom directory Fictional directory — unclosed tags follow. Amber Loom Oren Fenn works for Amber Loom. Contact Oren Fenn at oren.fenn@amber-loom.example. Directory effective 2026-09-01
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - observed_unknown_contact: oren.fenn@amber-loom.example
- Source reference: `artifact-4ac81b681f3f71617b542dc3901f253b` / `8c1ae181ae1de048d4d068ca81484443ff9994d6f3c7567f5685b465a0d1d889`

## redirect — success

- Requested URL: `http://127.0.0.1:37071/redirect`
- Input reference: `efda4aa2c5c7c08b4f540c466a939a23a1d228181972ec01a759afda5de91eb8`
- Text:
  - Lumé Lantern contact Lumé Lantern Néra Vale is the design lead at Lumé Lantern. Her published work contact is nera.vale@lume-lantern.example. Directory effective 2026-09-01. All people and companies on this page are fictional.
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - observed_unknown_contact: nera.vale@lume-lantern.example
- Source reference: `artifact-a844558669733d633e38cbbd44bb468a` / `d75f55711a996c3f878fa4f6b0f95037ae74e19237a844fe12ae22573162161a`

## empty — success

- Requested URL: `http://127.0.0.1:37071/empty`
- Input reference: `23b42e7b68463d4c30e081b217cfa97faba6f402df17c0998ef04acd3fdaad4e`
- Text:
  - _none_
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - _none_
- Source reference: `artifact-9cc8151dc2a9e7b53bf56a49dea8d925` / `ec0227f3f5814a69c76331dfeccd8315f205e18f49d50cdac7ef13e306a8006c`

## failure — failure

- Requested URL: `http://127.0.0.1:37071/failure`
- Input reference: `6a6b541c9ac96e935bb3ea024fa13def0a0b68f751d93023803b280bcc02c023`
- Error: `http_error` — The server returned an unsuccessful HTTP status.
- Text:
  - _none_
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - _none_
- Source reference: _unavailable for this failed or held occurrence_

## static-again — success

- Requested URL: `http://127.0.0.1:37071/static`
- Input reference: `196c32e045357876e45368079bec17e538c312ef7ef38881e602f97599c9fa67`
- Text:
  - Lumé Lantern archive Lumé Lantern Lumé Lantern publishes café lighting notes — résumé available. Lumé Lantern contact directory
- Observed links:
  - http://127.0.0.1:37071/named
- Typed candidates (unreviewed):
  - _none_
- Source reference: `artifact-4423f496e9fc68d6c80dc9e3ec9b165b` / `5e33eded4a2b9619420b7f39d49239d77889ab9b8727cc2ccc0bfb05b43c86fa`
