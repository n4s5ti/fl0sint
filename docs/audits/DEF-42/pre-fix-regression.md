# DEF-42 pre-fix regression evidence

## Captured red run

This run occurred after the direct-contract regression tests were added and before the follow-up repair of WebsiteToText scan/postprocess and structured cancellation.

~~~text
mpg-batch -- env PYTHONPATH=/home/n4s5ti/Documents/dev/fl0sint-def42-s02/flowsint-core/src:/home/n4s5ti/Documents/dev/fl0sint-def42-s02/flowsint-enrichers/src:/home/n4s5ti/Documents/dev/fl0sint-def42-s02/flowsint-types/src /home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python -m pytest /home/n4s5ti/Documents/dev/fl0sint-def42-s02/flowsint-enrichers/tests/enrichers/test_website_to_text.py -q
......FFF                                                                [100%]
~~~

The captured failures were:

1. Public scan returned Phrase objects, so scan then postprocess had no source envelope and accessing occurrence.source raised AttributeError.
2. execute_structured hashed the validated Website rather than the original raw string.
3. Cancelling the parent structured task propagated CancelledError from _scan_occurrence_specs.

## Missing historical evidence

[MISSING] No persisted raw stdout/stderr log or immutable source snapshot exists for the earlier pre-DEF-42 worktree state. This file does not infer its output.

[MISSING] The rejected draft scope-plan.md and acceptance.md were removed; they are not treated as acceptance evidence.
