# S05 recovery and review handoff

Source checkpoint: `0d00925565b2fe11e52b596011465f0126effcb1`, branch `work/def-45-observed-extraction`. S04 base: `709879ebc7a14fc09312bf1f8a6a7f637a528ddc`. No deployment occurred.

Run the core example with explicit core/src PYTHONPATH and `--url CONTROLLED_URL --runtime-config REVIEWED_FILE`. Saved mode uses `--proof ENVELOPE --runtime-config REVIEWED_FILE --operation ORIGINAL_OPERATION --occurrence ORIGINAL_OCCURRENCE`; caller/scope/source defaults must match the original proof. The runtime config is operator-owned, not a user-supplied retention grant.

Unavailable or revoked policy: return HOLD/REVIEW, do not substitute a graph/model client, fabricate empty success, refetch silently, reset elapsed allocation, or promote observed contacts. Repair reviewed current policy and trusted store availability, then resolve the same proof. Missing/corrupt bytes require a new explicitly authorized acquisition and immutable snapshot; never rewrite the original snapshot/digest or operation history.

Before downstream adoption, revert both S05 source commits (`0d009255`, then `20cc2596`) as units if rollback is required; preserve audit receipts and retained data. After adoption, coordinate consumers of strict metadata before rollback. Old untyped metadata must not receive a compatibility shim that bypasses validation. Never delete artifacts merely to make resolution succeed.

Human review should use acceptance.md, both independent reports, final host logs, exact raw-span smoke, and corrected impact receipts. Native author/post-impact narratives are not acceptance authorities. Doctor frontend typecheck and unsupported Hunt remain explicit limitations. No push or merge is authorized by this handoff.
