# Changelog

## [Unreleased]

### Added

- Agent Jury `claims check` job and `scripts/claims_check.py`: flags uncited guarantees, certifications, absolute claims, metrics and legal liabilities in added public text. The model classifies uncited hits. New inputs `claims_mode` (`enforce` default, or `report`) and `claims_ref`. A `claims: proven <evidence>` marker cites a claim.

- Kubernetes architecture documentation and generator tooling covering estate system boundaries, Rust type systems, and custom resource catalogs.

### Fixed

- Agent Jury request-build and response-parsing failures now emit explicit error-tier outputs and retain jq diagnostics; file-backed prompt and request data avoid command argument size limits.
