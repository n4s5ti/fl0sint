# Standalone scraper report

- Schema: `standalone-scraper/1.0`
- Operation: `standalone-website-to-text-368437e9c00f4ef9b00244586d1a3e3e`
- Scope: `local-web-fetch`
- Extraction: `observed-readable-text`
- Disposition: all links and candidates are **unreviewed observations**, not accepted contacts.

## slow — success

- Requested URL: `http://127.0.0.1:34583/slow`
- Input reference: `4404d4157d1ad56ab348cc7ed70cde54f8694054a7796e32eff4dbfbfa036ee6`
- Text:
  - Slow fixture This synthetic response is deliberately delayed beyond the case deadline.
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - _none_
- Source reference: `artifact-6439a0398f9d53712bd7d5b27d04157d` / `56256b2a0406dde7e93264d139ca126e1f5255912937c0b19ed7cec6af2c6010`

## fast — success

- Requested URL: `http://127.0.0.1:34583/static`
- Input reference: `6cd56a1f0a2c58c717f416c8d719a4cc1f9304ac1a94cb0499af7417f17f4c4d`
- Text:
  - Lumé Lantern archive Lumé Lantern Lumé Lantern publishes café lighting notes — résumé available. Lumé Lantern contact directory
- Observed links:
  - http://127.0.0.1:34583/named
- Typed candidates (unreviewed):
  - _none_
- Source reference: `artifact-edd6179aeb392470ecc96c76a063fdda` / `5e33eded4a2b9619420b7f39d49239d77889ab9b8727cc2ccc0bfb05b43c86fa`

## fail — failure

- Requested URL: `http://127.0.0.1:34583/failure`
- Input reference: `177cb3e4f4ad0d23d0b2d92124b1685bda4326695d4212376e761ece1025b81a`
- Error: `http_error` — The server returned an unsuccessful HTTP status.
- Text:
  - _none_
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - _none_
- Source reference: _unavailable for this failed or held occurrence_

## dup-1 — success

- Requested URL: `http://127.0.0.1:34583/named`
- Input reference: `37de142807bdc6910879e93ea76817b0d636bdc2b4f06bccde38630f2afa5de7`
- Text:
  - Lumé Lantern contact Lumé Lantern Néra Vale is the design lead at Lumé Lantern. Her published work contact is nera.vale@lume-lantern.example. Directory effective 2026-09-01. All people and companies on this page are fictional.
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - observed_unknown_contact: nera.vale@lume-lantern.example
- Source reference: `artifact-ba626c119409f549b3be595590d6741c` / `d75f55711a996c3f878fa4f6b0f95037ae74e19237a844fe12ae22573162161a`

## dup-2 — success

- Requested URL: `http://127.0.0.1:34583/named`
- Input reference: `37de142807bdc6910879e93ea76817b0d636bdc2b4f06bccde38630f2afa5de7`
- Text:
  - Lumé Lantern contact Lumé Lantern Néra Vale is the design lead at Lumé Lantern. Her published work contact is nera.vale@lume-lantern.example. Directory effective 2026-09-01. All people and companies on this page are fictional.
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - observed_unknown_contact: nera.vale@lume-lantern.example
- Source reference: `artifact-38e3cc7fc4db5c30a54a31040db7f897` / `d75f55711a996c3f878fa4f6b0f95037ae74e19237a844fe12ae22573162161a`

## copy-a — success

- Requested URL: `http://127.0.0.1:34583/copy-a`
- Input reference: `bf08a3106f0f3adf986680e1a9c2cabc728058bdc3e67bee80681322597a56b0`
- Text:
  - Lumé Lantern contact Lumé Lantern Néra Vale is the design lead at Lumé Lantern. Her published work contact is nera.vale@lume-lantern.example. Directory effective 2026-09-01. All people and companies on this page are fictional.
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - observed_unknown_contact: nera.vale@lume-lantern.example
- Source reference: `artifact-0b9557ee3911b90d0ffa6c71c5b61c51` / `d75f55711a996c3f878fa4f6b0f95037ae74e19237a844fe12ae22573162161a`

## copy-b — success

- Requested URL: `http://127.0.0.1:34583/copy-b`
- Input reference: `268aea27120bf59d1f6e25e46456a24a61f4ca24c0f9cd6538a0bd35c19337bc`
- Text:
  - Lumé Lantern contact Lumé Lantern Néra Vale is the design lead at Lumé Lantern. Her published work contact is nera.vale@lume-lantern.example. Directory effective 2026-09-01. All people and companies on this page are fictional.
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - observed_unknown_contact: nera.vale@lume-lantern.example
- Source reference: `artifact-69185b9ed3651f6035832258de600fca` / `d75f55711a996c3f878fa4f6b0f95037ae74e19237a844fe12ae22573162161a`

## redir — success

- Requested URL: `http://127.0.0.1:34583/redirect`
- Input reference: `e317cd99ea8b4c9d1aa7c5727d5c8d03cef0fcad3cc4b6ed4b9e65e20b899e9d`
- Text:
  - Lumé Lantern contact Lumé Lantern Néra Vale is the design lead at Lumé Lantern. Her published work contact is nera.vale@lume-lantern.example. Directory effective 2026-09-01. All people and companies on this page are fictional.
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - observed_unknown_contact: nera.vale@lume-lantern.example
- Source reference: `artifact-7402f5618aab278a8d4dacab1fa72321` / `d75f55711a996c3f878fa4f6b0f95037ae74e19237a844fe12ae22573162161a`

## empty — success

- Requested URL: `http://127.0.0.1:34583/empty`
- Input reference: `a97c887c0ff782d4b28abab7a3fe26afd17e3df97c721a750eb2c8a026e655d4`
- Text:
  - _none_
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - _none_
- Source reference: `artifact-ed1e28d67d6db5b8c36f65efb44d0947` / `ec0227f3f5814a69c76331dfeccd8315f205e18f49d50cdac7ef13e306a8006c`

## malformed — success

- Requested URL: `http://127.0.0.1:34583/malformed`
- Input reference: `3620654936827ccc1c7f1139573cffeb2c08c3214887207599af536afe3d5ee3`
- Text:
  - Amber Loom directory Fictional directory — unclosed tags follow. Amber Loom Oren Fenn works for Amber Loom. Contact Oren Fenn at oren.fenn@amber-loom.example. Directory effective 2026-09-01
- Observed links:
  - _none_
- Typed candidates (unreviewed):
  - observed_unknown_contact: oren.fenn@amber-loom.example
- Source reference: `artifact-13ea0d678b6b93b20cdc6705fd310ceb` / `8c1ae181ae1de048d4d068ca81484443ff9994d6f3c7567f5685b465a0d1d889`
