# .github
Org profile

## Jury workflow tests

Run `python3 -m pip install PyYAML==6.0.2`, then
`python3 -m unittest discover -s tests -v`.
The harness extracts and executes the actual reusable workflow shell with real
jq and a local curl stub. It uses synthetic diffs, no credentials or network.
It checks successful full/fast reviews, oversized prompts, request-build and
response-parsing failures, and the existing error comment/label/delivery gate.

For a break-check against a saved older workflow:
`JURY_WORKFLOW=/tmp/old-agent-jury.yml python3 -m unittest discover -s tests -v`.
Failure assertions should go red on an unguarded build/parser; the successful
review control should still pass.

Changes here do not reach callers pinned to `@v1` until an operator explicitly
updates that tag or changes caller refs. Test changes on a canary caller before
an estate-wide rollout.
