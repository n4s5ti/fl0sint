# Enricher-to-Sheet Column Mapping

## Target: Peptide Buyers (main tab)
Spreadsheet ID: `1RS4Z0PXO_dbbkNhPoHLJ5PiWpYSUEfAL6Fvc2XLOOUc`

## Column Gaps & Enricher Coverage

| Col | Header | Status | Enricher | Output Field(s) | Priority |
|-----|--------|--------|----------|-----------------|----------|
| D | phone | EMPTY | `email_to_intelligence` (DeHashed) | `Individual.phone_numbers: List[Phone]` | P1 |
| G | linkedin | EMPTY | `email_to_intelligence` (DeHashed) | `Individual.social_media_profiles: List[str]` | P1 |
| H | address | EMPTY | Chain: `email_to_domain` -> `domain_to_whois` | `Location.street_address` from whois | P2 |
| I | city | EMPTY | Chain: `email_to_domain` -> `domain_to_whois` | `Location.city` from whois | P2 |
| J | state | EMPTY | Chain: `email_to_domain` -> `domain_to_whois` | Custom extract from whois | P2 |
| K | zip | EMPTY | Chain: `email_to_domain` -> `domain_to_whois` | `Location.zip` from whois | P2 |

## Best First Enricher: `email_to_intelligence`

- **Category:** Email
- **Input:** `Email` (email address from column A)
- **Output:** `Individual`
- **Key output fields mapped to sheet:**
  - `phone_numbers: List[Phone]` -> col D (phone)
  - `social_media_profiles: List[str]` -> col G (linkedin, when contains linkedin.com URL)
  - `full_name: str | null` -> cols B/C (already filled)
- **API:** DeHashed (POST `https://api.dehashed.com/v2/search`)
- **Auth:** `DEHASHED_API_KEY` via vault or env
- **Current state:** NOT in `.env` -- must be provided before enricher works.

## Address Data (Chain)

| Step | Enricher | I/O | Purpose |
|------|----------|-----|---------|
| 1 | `email_to_domain` | Email -> Domain | Extract @domain.com |
| 2 | `domain_to_whois` | Domain -> Whois | Registrant contact data |
| 3 | Parse | Whois -> address/city/state/zip | Extract location fields |

## Social Discovery Chain

| Step | Enricher | I/O | Purpose |
|------|----------|-----|---------|
| 1 | `email_to_username` | Email -> Username | Extract prefix from email |
| 2 | `username_to_socials_blackbird` | Username -> SocialAccount | Full social scan |
| 2a | `username_to_socials_maigret` | Username -> SocialAccount | Maigret scan |
| 3 | `social-confidence-aggregator` | SocialAccount[] -> SocialAccount[] | Dedup + cross-validation |

## Integration Architecture

Option A: Direct API (automated)
  gog sheets get email -> POST /api/enrichers/.../launch -> poll -> gog sheets update

Option B: Wayland CLI (desktop automation)
  wayland key/mouse -> navigate Fl0sint UI -> run enricher -> paste to sheet

Option C: Celery (existing pipeline)
  POST /api/enrichers/{name}/launch -> Celery task -> scan() -> postprocess

## Credentials Needed

| Secret | Used By | Source |
|--------|---------|--------|
| `DEHASHED_API_KEY` | `email_to_intelligence` | Vault or .env -- missing |
| gog OAuth | Sheet read/write | Already authed as michael@agentsec.ai |
