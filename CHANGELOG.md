# Changelog

## [Unreleased]

### Added

- Agent Jury `claims check` job and `scripts/claims_check.py`: flags uncited guarantees, certifications, absolute claims, metrics and legal liabilities in added public text. The model classifies uncited hits. New input `claims_mode` (`enforce` default, or `report`). The script is checked out at `job.workflow_sha`. A `claims: proven <evidence>` marker cites one claim, and its evidence must resolve: a repo path at the PR head, a commit SHA in the repo, or an https URL on an allowlisted host. New input `claims_url_hosts` (default `github.com`). The job never fetches a URL. Added lines are normalised (HTML entities, NFKC, zero-width and soft-hyphen characters, look-alike letters) before matching. New categories `unreadable` and `obfuscation`. Scope is case-insensitive and adds `.htm`, `.markdown`, `.rst`, `README*`, `pricing/` and `landing/`. The job fails closed with exit code 2 on a malformed model answer, under `bash -e` as on the runner.

- Agent Jury `claims check` hardening (round 3). Evidence must be a regular, non-empty blob at the base branch tip that the PR does not change, and a cited SHA must be reachable from base; new input `claims_evidence_paths`. Markup that splits a word (HTML comments and tags, emphasis, escapes, link syntax) is removed before matching. Combining marks and blank-rendering characters are dropped (NFKD). Look-alike letters fold to Latin in every word. New input `claims_paths` replaces the default scope; the default adds `locales/`, `i18n/`, `*.njk` and `*.liquid` and leaves out licence files. The job diff uses `--no-renames`. Two consecutive added lines are matched as one phrase. A marker cites the next line only when it holds the whole line. The lexicon gains 11 terms. A repeated index in the model answer fails closed with exit code 2.

- Kubernetes architecture documentation and generator tooling covering estate system boundaries, Rust type systems, and custom resource catalogs.

### Fixed

- Agent Jury request-build and response-parsing failures now emit explicit error-tier outputs and retain jq diagnostics; file-backed prompt and request data avoid command argument size limits.
