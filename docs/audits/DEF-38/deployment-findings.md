# DEF-38 — Deployment findings (image ↔ source comparison)

Revision `59e2d670f674b8cf7b32f19ef9e7f8d895d4422f`. Read-only; nothing below was fixed. No images were pulled and no registry was queried (no network/live-data collection in this pass), so registry-side facts are marked UNKNOWN.

## F1 — `docker-compose.prod.yml` pulls **upstream** images, not this fork's (HIGH)
- `docker-compose.prod.yml:66,99,146` → `ghcr.io/reconurge/flowsint-api:${FLOWSINT_VERSION:-latest}` and `ghcr.io/reconurge/flowsint-app:${FLOWSINT_VERSION:-latest}`.
- `Makefile:prod` → `docker compose -f docker-compose.prod.yml pull` then `up -d` — i.e. `make prod` never builds from this tree.
- This repo's origin is `https://github.com/n4s5ti/fl0sint.git` (`git remote -v`); `reconurge/flowsint` is the `upstream` remote. `.github/workflows/images.yml:47-48,127-128` publish to `ghcr.io/${{ github.repository_owner }}/flowsint-{app,api}` → for this fork that is `ghcr.io/n4s5ti/…`, which prod compose never references.
- Consequence: a `make prod` deployment runs upstream code; none of the 13 fork commits (`git rev-list --count origin/main..HEAD`) — including `to_text.py` QUIC/GPU work, registry changes, graph projection connectors — are in the running containers. **This is the image/source mismatch the issue anticipates; recorded, not fixed.**
- Whether `ghcr.io/n4s5ti/flowsint-*` images exist at all: UNKNOWN (not queried).

## F2 — Image tag is unpinned (`latest`) and no tag corresponds to HEAD (MEDIUM)
- `FLOWSINT_VERSION` defaults to `latest` (`docker-compose.prod.yml:66`; `README.md:111` documents pinning via `.env`). `.env.example` does not set it.
- `images.yml` only builds on `push: tags: v*`. `git describe --tags` = `v1.2.11-25-g59e2d67`: HEAD is 25 commits past the newest tag `v1.2.11`, so no CI-built image can equal HEAD source.
- Source version strings disagree with the latest tag: `pyproject.toml` and all four package `pyproject.toml` say `1.2.8`; `flowsint-app/package.json` and root `package.json` say `1.0.0`; tags go up to `v1.2.11`. `scripts/sync-versions.js` exists but has evidently not been run for the last three tags.

## F3 — OCI source label inside the API image points at upstream (LOW)
- `flowsint-api/Dockerfile:75` → `LABEL org.opencontainers.image.source="https://github.com/reconurge/flowsint"`, while `flowsint-app/Dockerfile:25` says `https://github.com/n4s5ti/fl0sint`. Inconsistent provenance across the two images.

## F4 — Compose/Makefile assumptions vs source (INFO)
- Prod `celery` service overrides the Dockerfile HTTP healthcheck with `celery inspect ping` (`docker-compose.prod.yml:126-131`) — consistent with source (Celery has no HTTP server). OK.
- Prod `app` bind-mounts `./flowsint-app/nginx.conf` over the image's nginx.conf (`:154`); file exists at HEAD. OK, but means the *running* nginx config is source-tree, not image — a partial source/image split even for the upstream image.
- Prod API/Celery mount `/var/run/docker.sock:ro` (`:73,:109`) — required by `docker>=7.1` dependency in `flowsint-core/pyproject.toml`; security-relevant, unchanged.
- `Makefile:test` (four `uv run pytest` invocations) matches `.github/workflows/tests.yml` (`make test`). OK.
- `Makefile:celery` runs `celery -A flowsint_core.core.celery` — matches `docker-compose.prod.yml:100-108` command. OK.
- `docker-compose.dev.yml`/`e2e.yml` build from source (`flowsint-api/Dockerfile` target `dev`/`production`), so dev/e2e do reflect the tree; only `prod` does not.

## F5 — Runtime env contract differences dev vs prod (INFO)
- Prod sets `SKIP_MIGRATIONS=true` only for `celery` (`:120`); `flowsint-api/entrypoint.sh:7-11` runs `alembic upgrade head` in the API container on every start. Untracked `flowsint-api/alembic/versions/14b9218242a4_add_grievances_table.py` in the dirty tree would run on prod boot if committed and an image were built — noted for later slices.
- `MASTER_VAULT_KEY_V1`, `NEO4J_USERNAME/PASSWORD`, `AUTH_SECRET` have no defaults in prod compose (good) but `.env.example` ships fixed example values including a vault key — a deployer copying `.env.example` (which `make check-env` does automatically, `Makefile:20-29`) gets a known key. Preexisting.

## Summary table
| ID | Finding | Severity | Fixed? |
|---|---|---|---|
| F1 | prod compose pulls `ghcr.io/reconurge/*` (upstream), fork source never deployed by `make prod` | HIGH | no — recorded |
| F2 | `latest` tag, HEAD is 25 commits past `v1.2.11`, version strings 1.2.8/1.0.0 inconsistent | MEDIUM | no |
| F3 | API image OCI source label → upstream repo | LOW | no |
| F4 | nginx.conf bind-mount makes prod partly source-tree | INFO | no |
| F5 | `.env.example` fixed secrets auto-copied by `make check-env` | INFO | no |
