# DEF-42 provenance reverification

## Result

**Archive provenance: RESOLVED.** An independent read-only comparison reproduced the coordinator's provenance result. The original repository resolved `8de569a7` to `8de569a731fca084d05d89a6316daafb5a34caa3`. A fresh `git archive --format=tar 8de569a7` contained 1,349 regular files. Every regular archive member matched the corresponding regular file under `./repo` byte for byte; mismatch count: 0.

- Commit: `8de569a731fca084d05d89a6316daafb5a34caa3`
- Archive SHA-256: `20c0f69cfd4ee2ae475f2cd3bc34b95450aef22be664e553723d5ac603ee7d8f`
- Regular files checked: 1,349
- Mismatches: 0
- Deterministic content-manifest SHA-256: `0a710fd8872b21f34ee6714a8197073d9615c0560e01bea2cbc314cabf17f4ac`

The content-manifest digest above is SHA-256 over each archive-order regular member encoded as `path`, NUL, the member-content SHA-256 in lowercase hexadecimal, and newline. The independently computed archive count and archive digest agree with `provenance-check.json`.

## Recovery receipts

The following receipts exist under `/tmp/def42-recovery` and were inspected. These are **coordinator-observed results**, not independent sandbox socket or runtime executions:

- Head package: `head-package-tests.json` records return code 0; `head-package-tests.stdout` reports **165 passed, 2 warnings**.
- Baseline package: `baseline-package-tests.json` records return code 0; `baseline-package-tests.stdout` reports **156 passed, 2 warnings**.
- Source-pinned live HTTP smoke: `live-smoke.stdout` identifies the head `website/to_text.py`, records `real loopback HTTP`, and reports **PASS**.
- Head drift: `head-final-drift.json` records return code 0; `head-final-drift.stdout` reports total drift **0**.
- Baseline drift: `baseline-drift.json` records return code 0; `baseline-drift.stdout` reports total drift **0**.

The package-test stderr receipts also contain refused optional PostgreSQL log-sink connections, but both pytest commands returned 0 and their stdout records the passing counts above.

## Preserved evidence-contract verdict

**Historical pre-edit receipt waiver: UNRESOLVED. Overall evidence verdict remains BLOCKED / DO NOT SHIP if the historical receipt is a mandatory acceptance gate.**

This provenance proof establishes that the reviewed extracted tree corresponds byte for byte, for every regular archive member, to commit `8de569a7`. It does not create, repair, backdate, or substitute for the immutable historical PRE-EDIT Blast receipt that the manifest says was not preserved. Retrospective baseline/head evidence and the coordinator recovery receipts remain retrospective or coordinator-observed evidence. An explicit user waiver is still required if the missing historical receipt is mandatory.

The functional PASS stated in `REPORT.md` is unchanged. `REPORT.md` and `SUMMARY.txt` were not modified.

## Commands and method

```sh
git -C /home/n4s5ti/Documents/dev/fl0sint-def42-s02 rev-parse 8de569a7
git -C /home/n4s5ti/Documents/dev/fl0sint-def42-s02 archive --format=tar 8de569a7
```

The fresh archive was written only to `/tmp/def42-independent-8de569a7.tar`. A read-only Python tar iterator selected regular members, calculated the archive and content digests, and compared each member's bytes with `/home/n4s5ti/.cache/def42-independent/repo/<member path>`. No write was made to the original repository. Source trees outside this scratch directory were treated as read-only. No network or indexing operation was used.
