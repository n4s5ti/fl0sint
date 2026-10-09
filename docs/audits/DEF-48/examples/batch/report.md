# Standalone scraper report

- Schema: `standalone-scraper/1.0`
- Operation: `standalone-website-to-text-3b89086f917a4f928dc5a520e172bced`
- Scope: `local-web-fetch`
- Extraction: `observed-readable-text`
- Disposition: all links and candidates are **unreviewed observations**, not accepted contacts.

## static — success

- Requested URL: `http://127.0.0.1:33453/static`
- Input reference: `2f9c1eafcaef6dfa576cf9d1dafdd1eea8eb567dfaa9acb5b7a8aa1860bed4f3`
- Text:
  - Lumé Lantern archive Lumé Lantern Lumé Lantern publishes café lighting notes — résumé available. Lumé Lantern contact directory
- Observed links:
  - http://127.0.0.1:33453/named
- Typed candidates (unreviewed):
  - _none_
- Source reference: `artifact-4051806e7496bada1528fb890a7d5557` / `5e33eded4a2b9619420b7f39d49239d77889ab9b8727cc2ccc0bfb05b43c86fa`

## named — success

- Requested URL: `http://127.0.0.1:33453/named`
- Input reference: `b12405e393819367d83f1039ff435b6a13c677884ef9a9d64d2ee4fc49f661ae`
- Text:
  - Lumé Lantern contact Lumé Lantern Néra Vale is the design lead at Lumé Lantern. Her published work contact is nera.vale@lume-lantern.example. Directory effective 2026-09-01. All people and companies on this page are fictional.
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - observed_unknown_contact: nera.vale@lume-lantern.example
- Source reference: `artifact-2c0ed2c2f4578a163be2932736b3bbaa` / `d75f55711a996c3f878fa4f6b0f95037ae74e19237a844fe12ae22573162161a`

## malformed — success

- Requested URL: `http://127.0.0.1:33453/malformed`
- Input reference: `4559cb04507ec490476411b0e879a4d4639f1d7b7ec15238ba8179d04ca3b62b`
- Text:
  - Amber Loom directory Fictional directory — unclosed tags follow. Amber Loom Oren Fenn works for Amber Loom. Contact Oren Fenn at oren.fenn@amber-loom.example. Directory effective 2026-09-01
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - observed_unknown_contact: oren.fenn@amber-loom.example
- Source reference: `artifact-4f7c861ebf9095df8e0beee673fcff33` / `8c1ae181ae1de048d4d068ca81484443ff9994d6f3c7567f5685b465a0d1d889`

## redirect — success

- Requested URL: `http://127.0.0.1:33453/redirect`
- Input reference: `386418c867633805ff9e7798e6c284ebb75969b8d38cb3b9090fdac05a62834b`
- Text:
  - Lumé Lantern contact Lumé Lantern Néra Vale is the design lead at Lumé Lantern. Her published work contact is nera.vale@lume-lantern.example. Directory effective 2026-09-01. All people and companies on this page are fictional.
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - observed_unknown_contact: nera.vale@lume-lantern.example
- Source reference: `artifact-91596f9ee2b8985f49a4b1be14739e91` / `d75f55711a996c3f878fa4f6b0f95037ae74e19237a844fe12ae22573162161a`

## empty — success

- Requested URL: `http://127.0.0.1:33453/empty`
- Input reference: `75cd91b47dde50cb9d3415b43d5ca8e1ef79587a612b5d0b89375af52c6d0c96`
- Text:
  - _none_
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - _none_
- Source reference: `artifact-d357d4342e84d5d37f9684820d9150b0` / `ec0227f3f5814a69c76331dfeccd8315f205e18f49d50cdac7ef13e306a8006c`

## failure — failure

- Requested URL: `http://127.0.0.1:33453/failure`
- Input reference: `9b7f468c3889a9265e5a738bdf64149d235ebf2a2c5bdd7c6aee1ee7fabebf5f`
- Error: `http_error` — The server returned an unsuccessful HTTP status.
- Text:
  - _none_
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - _none_
- Source reference: _unavailable for this failed or held occurrence_

## static-again — success

- Requested URL: `http://127.0.0.1:33453/static`
- Input reference: `2f9c1eafcaef6dfa576cf9d1dafdd1eea8eb567dfaa9acb5b7a8aa1860bed4f3`
- Text:
  - Lumé Lantern archive Lumé Lantern Lumé Lantern publishes café lighting notes — résumé available. Lumé Lantern contact directory
- Observed links:
  - http://127.0.0.1:33453/named
- Typed candidates (unreviewed):
  - _none_
- Source reference: `artifact-4a90bc9fef89858e36886d77797cb298` / `5e33eded4a2b9619420b7f39d49239d77889ab9b8727cc2ccc0bfb05b43c86fa`
