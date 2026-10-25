# BEFORE Graph/Blast Evidence Summary

## Execution Metadata
- Worktree: `/home/n4s5ti/Documents/dev/fl0sint-def41-s01`
- Git SHA: `1b48c428e2e32a5179005df288ac669bdae82409`
- Tag: `v1.2.11-33-g1b48c428`
- Execution Date: 2026-09-26
- Main repo location: `/home/n4s5ti/Documents/dev/fl0sint`

## Package Lock Hashes
- package-lock.json: `3d249a76c83e24d071c2d7f700c85064ce4139dd3dbc1519401e53ec7ad5b62a`
- yarn.lock: `33294b53f39d22632468c3c8fb1f71bda36e58c61bd801ce4285a2eff90c931c`
- uv.lock: `e62c316447022773fcbeb958615be3fe8eec149f57771746ed52e692e28bbb4f`
- pyproject.toml: `2cecd6e43aafe31ca26103751b4c4e5673f34bb3de26663d18107c43e98ae1e3`

## Index Operation
- Command: `pip3r graphos --index --cwd /home/n4s5ti/Documents/dev/fl0sint-def41-s01 --json`
- Exit Code: 0
- Status: SUCCESS
- Generation SHA: `sha256:dbef0d67348a684550e90ffe4ef47072b74ff68c82a33c00ca5e4309caefb68c`
- Files Parsed: 1068
- Latency: 33990 ms (~34 seconds)
- Delta Changes: 0 (no modifications, additions, or deletions)
- Warnings: "No workspace packages with recognised entry files — sidecar will be empty"

## Blast Analysis Results

### Target: InputOutcome

#### Upstream Direction
- Exit Code: 0
- Output Size: 5179 bytes
- Coverage: Extracted below

#### Downstream Direction
- Exit Code: 0
- Output Size: 16688 bytes
- Coverage: Extracted below

### Target: EvidenceEnvelope

#### Upstream Direction
- Exit Code: 0
- Output Size: 6906 bytes
- Coverage: Extracted below

#### Downstream Direction
- Exit Code: 0
- Output Size: 35345 bytes
- Coverage: Extracted below

### Target: StructuredExecutionResult

#### Upstream Direction
- Exit Code: 0
- Output Size: 3243 bytes
- Coverage: Extracted below

#### Downstream Direction
- Exit Code: 0
- Output Size: 6873 bytes
- Coverage: Extracted below

## Artifact Files Generated

### Metadata
- `graphos-index-argv.json` - Command arguments for index operation
- `graphos-index-rc.txt` - Return code
- `graphos-index-stdout.json` - Index output
- `graphos-index-stderr.txt` - Index stderr (empty)
- `worktree-versions.txt` - Worktree SHA and package hashes
- `main-gitnexus-hashes.txt` - Main repo .gitnexus file hashes

### Blast Results (6 files each: argv, rc, stdout, stderr)
- blast-InputOutcome-upstream-{argv.json,rc.txt,stdout.json,stderr.txt}
- blast-InputOutcome-downstream-{argv.json,rc.txt,stdout.json,stderr.txt}
- blast-EvidenceEnvelope-upstream-{argv.json,rc.txt,stdout.json,stderr.txt}
- blast-EvidenceEnvelope-downstream-{argv.json,rc.txt,stdout.json,stderr.txt}
- blast-StructuredExecutionResult-upstream-{argv.json,rc.txt,stdout.json,stderr.txt}
- blast-StructuredExecutionResult-downstream-{argv.json,rc.txt,stdout.json,stderr.txt}

## Data Integrity Notes
- All commands executed with PIP3R_DISCLOSURE=off for raw JSON output
- Serial execution (no concurrent graph/blast commands to avoid LadybugDB contention)
- All exit codes verified as 0 (success)
- No stderr output from any command
- No --auto-index passed (read-only audit mode with --no-auto-index)
- Index drift verified: deltaAdded=0, deltaModified=0, deltaDeleted=0

