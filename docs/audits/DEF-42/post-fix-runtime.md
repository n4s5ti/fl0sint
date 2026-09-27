# DEF-42 post-fix runtime evidence

## Targeted WebsiteToText regression suite

~~~text
mpg-batch -- env PYTHONPATH=/home/n4s5ti/Documents/dev/fl0sint-def42-s02/flowsint-core/src:/home/n4s5ti/Documents/dev/fl0sint-def42-s02/flowsint-enrichers/src:/home/n4s5ti/Documents/dev/fl0sint-def42-s02/flowsint-types/src /home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python -m pytest /home/n4s5ti/Documents/dev/fl0sint-def42-s02/flowsint-enrichers/tests/enrichers/test_website_to_text.py -q
.........                                                                [100%]
9 passed, 2 warnings in 3.83s
~~~

Exercised behavior: loopback slow/fast public execute; direct scan yielding public WebsiteTextOccurrence envelopes then typed postprocess graph capture; no flat-Phrase postprocess fallback; middle and leading failure attribution; successful empty extraction; duplicate/reinvocation history; one-to-many graph edges; raw-input structured hash; parent cancellation retained as held outcomes; and a separate-process JSON consumer.

## Related fixture suite

~~~text
mpg-batch -- env PYTHONPATH=/home/n4s5ti/Documents/dev/fl0sint-def42-s02/flowsint-core/src:/home/n4s5ti/Documents/dev/fl0sint-def42-s02/flowsint-enrichers/src:/home/n4s5ti/Documents/dev/fl0sint-def42-s02/flowsint-types/src /home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python -m pytest /home/n4s5ti/Documents/dev/fl0sint-def42-s02/flowsint-enrichers/tests/enrichers/test_acquisition_fixtures.py -q
........................                                                 [100%]
24 passed, 2 warnings in 2.85s
~~~

Both runs emitted only the existing passlib crypt and Pydantic class-config deprecation warnings.


## Post-change impact and drift

Targeted source searches found no WebsiteToText references in flowsint-core or flowsint-api. Within flowsint-enrichers, the only references are the WebsiteToText definition and its dedicated regression test; no in-repository direct scan-to-postprocess caller required migration. The developer guide documents the typed occurrence-envelope contract for external direct callers.

The targeted source search found no zip, as_completed, dedup, enable_gpu, gpu_provider, or _gpu remnants in website/to_text.py. git diff --check against cbad468df16b0e6f3046379b4f33931b613e1b60 completed with no output. The tracked diff at capture time was 303 insertions and 144 deletions across CHANGELOG.md, managing-enrichers.mdx, and website/to_text.py; the audit evidence directory and regression test are untracked additions.

[UNKNOWN—external tool gate] Pip3r Blast invoked with the worktree-local lbug path returned stale pre-edit WebsiteToText content (GPU parameters and as_completed) rather than the current source, so it is not used as post-change impact evidence.
