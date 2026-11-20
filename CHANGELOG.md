# Changelog

## [Unreleased]

### Added

- Agent Jury `claims check` job and `scripts/claims_check.py`: flags uncited guarantees, certifications, absolute claims, metrics and legal liabilities in added public text. The model classifies uncited hits. New input `claims_mode` (`enforce` default, or `report`). The script is checked out at `job.workflow_sha`. A `claims: proven <evidence>` marker cites one claim, and its evidence must resolve: a repo path at the PR head, a commit SHA in the repo, or an https URL on an allowlisted host. New input `claims_url_hosts` (default `github.com`). The job never fetches a URL. Added lines are normalised (HTML entities, NFKC, zero-width and soft-hyphen characters, look-alike letters) before matching. New categories `unreadable` and `obfuscation`. Scope is case-insensitive and adds `.htm`, `.markdown`, `.rst`, `README*`, `pricing/` and `landing/`. The job fails closed with exit code 2 on a malformed model answer, under `bash -e` as on the runner.

- Kubernetes architecture documentation and generator tooling covering estate system boundaries, Rust type systems, and custom resource catalogs.

### Fixed

- Agent Jury request-build and response-parsing failures now emit explicit error-tier outputs and retain jq diagnostics; file-backed prompt and request data avoid command argument size limits.
