# GitHub-hosted run: not observed

No branch was pushed and no PR was opened for DEF-90, because pushing and merging require
the user's approval. The hosted **Tests / Audit packet validity** run is therefore UNKNOWN.

The local substitutes, which are not equivalent to a hosted run, are:
- `raw/post/ci-workflow-trace.json`: a parse of the committed workflow showing the triggers,
  the checkout ref and history depth, the exact validator command, and report publication.
- The validator's own tests run through `make test` -> the enrichers pytest, which is the
  existing `test` job.
- `raw/post/ci-local-replay.txt` (added in the packet commit): the job's exact `run:` script,
  executed in a clean clone of the packet commit with `BASE_SHA=a59bdadf`.

To close this item, push the branch or open a PR, then attach the job URL and the
`audit-packet-report` artifact.
