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

## Pinning and releases

The current release is **v1.1.1** (`a868a09b27976af8239cd295a6937c966f6b1f91`, tag `v1.1.1`). It writes the PR title, body and file list to `GITHUB_OUTPUT` under a random per-run delimiter, so a PR body line `EOF` can no longer inject step outputs (#13). Pin callers by full commit SHA. The previous release, **v1.1.0** (`c6567996e98d6c2228262646749821ce4f6522cd`), added the error tier for request-build and parse failures (CER-2146) and was canaried on cerebral-work/vilicus#36. Pin callers to the release commit by SHA, with the tag in a trailing comment. The moving `v1` tag still points at the older `701db71` and will be retired or moved only by an explicit operator decision.

## Dependabot runs need their own key

A pull request opened by Dependabot runs with Dependabot secrets, not Actions secrets. On a private repo that runs Dependabot, the caller therefore needs `AGENT_JURY_API_KEY` stored as a **Dependabot** secret as well (`gh secret set AGENT_JURY_API_KEY --app dependabot`). Without one, the gateway answers 401 "No api key passed in", seen on reverie #1969. With a stale key you get 403 `key_model_access_denied`, seen on vilicus#32. The job log line `Secret source: Dependabot` shows which store a run used.
