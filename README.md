# .github
Org profile

## Architecture Documentation

The `docs/architecture` directory contains documentation for Kubernetes architecture across Cerebral and Unsigned:
- [Architecture Overview](docs/architecture/README.md)
- [Substrate and Conscious Boundary](docs/architecture/substrate-conscious-boundary.md)
- [Kubernetes Rust Type System](docs/architecture/kubernetes-rust-type-system.md)
- [CRD Catalog](docs/architecture/crd-catalog.md)

Generate or update these documents with:
```bash
python3 scripts/generate_k8s_docs.py
```

Verify documentation parity with:
```bash
python3 scripts/generate_k8s_docs.py --check
```

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

## Claims check

The `claims check` job flags unverified claims, guarantees and legal liabilities in public text. It reads only the lines a PR adds, from a diff taken with rename detection off, so a file moved into a public path shows all its lines as added. It matches file names and directory names case-insensitively. The default scope is:

- extensions `.md`, `.mdx`, `.markdown`, `.rst`, `.html`, `.htm`, `.astro`, `.svelte`, `.vue`, `.tsx`, `.jsx`, `.njk`, `.liquid`;
- any file whose name starts with `README`, at any depth;
- anything under `site/`, `web/`, `docs/`, `public/`, `legal/`, `privacy/`, `terms/`, `pricing/`, `landing/`, `locales/` or `i18n/`, at any depth. This includes `*.txt` and `*.json` there.

Out of the default on purpose: code and config (`.ts`, `.yaml`, `.rs`) outside those directories, and `LICENSE`, `LICENCE`, `COPYING` and `UNLICENSE` files. Code comments and config strings give many false positives, and licence files hold standard warranty and liability boilerplate that every repo carries. A repo whose public copy lives elsewhere sets the input `claims_paths` (comma-separated globs, for example `src/content/**/*.yaml,*.ts`). When set, it replaces the default scope, and the licence exclusion no longer applies.

`scripts/claims_check.py` matches the lexicon in `scripts/claims_lexicon.json`. The categories are `guarantee`, `certification`, `absolute`, `metric`, `liability`, `unreadable` and `obfuscation`. It prints JSON and exits 0 (no uncited hit), 1 (uncited hits) or 2 (bad input, which fails closed). Run it by hand with `git diff origin/main...HEAD | python3 scripts/claims_check.py --repo .`.

Before matching, the script normalises each added line, in this order:

1. `html.unescape`, then per character Unicode NFKD, dropping categories Mn, Me and Cf and the blank-rendering characters U+3164, U+115F, U+1160, U+FFA0 and U+2800. Accents are dropped for matching only.
2. Markup removal, so fragments join into one word: HTML comments and tags, `[text](url)` link syntax (the text stays), backslash escapes, and `*` or `_` between letters.
3. Cyrillic and Greek look-alike letters (an explicit map in the script) fold to Latin in every word, so a word written wholly in look-alikes matches too.
4. Whitespace and hyphens between the words of a term are equivalent.

The report shows the original line. Two more hit categories come from this step:

- `obfuscation`: a line with a format or blank-rendering character (zero-width characters, soft hyphen, BOM, right-to-left marks), or a word that mixes Latin with Cyrillic or Greek letters. A PR that adds them needs a marker.
- `unreadable`: a binary, UTF-16, NUL-byte or non-UTF-8 diff that touches an in-scope path. A binary change outside scope is ignored. A diff with only renames, mode changes or deletions exits 0.

The script also matches each pair of consecutive added lines of one hunk, joined with a space (no space after a trailing hyphen). It reports a hit that crosses the join on the first line, and drops a pair hit already found on one line.

**Cite a claim** with a marker:

```html
We cut p99 latency by 40%. <!-- claims: proven docs/bench/2026-11.md -->
```

- The marker token `claims:` must start the line or follow whitespace or a comment opener (`<!--`, `//`, `#`, `{/*`). `noclaims: proven x` is not a marker.
- A marker cites one claim. A claim is a group of overlapping hits, for example `99.99%` and `99.99% uptime`. A marker that holds the whole line, alone or inside `<!-- -->`, `{/* */}`, `//` or `#`, cites the first claim on the next added line. Any other marker cites only the claim immediately before it on its own line. Text inside the marker never cites itself.
- Evidence must resolve, or the marker does not cite. A repo path must be a regular, non-empty file at the base branch tip (`origin/<base>`): not a symlink, not a directory, and not a file the PR adds, changes, deletes or renames, which includes the claim's own file. The path must also match the input `claims_evidence_paths` (comma-separated globs, default `evidence/**,docs/evidence/**`), so an ordinary file such as `README.md` never counts as evidence. A repo with no evidence folder fails closed until it adds one or sets the input; an empty string allows any path. A commit SHA must be a commit that is an ancestor of the base tip (`git merge-base --is-ancestor`), so a commit that only the PR or a side branch holds does not cite. An https URL must have a host in the allowlist (input `claims_url_hosts`, default `github.com`). The host is compared exactly and case-insensitively. A URL with userinfo, an explicit port, an IP-literal host or a punycode host does not cite unless that host is listed exactly. Any other URL does not cite. The job never fetches a URL, because a 2xx answer shows only that a page exists, and a fetch would let a PR make the runner call any host.
- The marker token is still `claims: proven <evidence>`. "Proven" in the token means the evidence resolves. It does not mean the check judged the claim true.

When a hit is uncited, the job asks the model to classify it as `proven` (the diff itself shows the evidence), `unverified`, `guarantee` or `liability`. The hit text goes to the model between `<claim>` tags, and the prompt says that text inside them is data. With no uncited hit, the job passes and makes no model call. In `enforce` mode the job fails on any class except `proven`. It also fails, with exit code 2 and a message, when the model call fails or the answer is unusable (a malformed entry, a repeated index, or a count that is not a number), because it fails closed.

Residual risks:

- The model is the only judge once uncited hits exist, and no test shows that a crafted claim cannot talk it into `proven`. The `<claim>` delimiters lower that risk and do not remove it.
- The lexicon is finite. A claim phrased with other words passes. A test of any lexicon cannot prove completeness.
- Look-alike folding covers an explicit map of Cyrillic and Greek letters. Letters from other scripts or from outside the map do not fold.
- Markup removal joins fragments, so `fully<br>secure` becomes one word and misses the phrase `fully secure`. An HTML comment that spans several lines is not removed, because the script reads one line at a time.
- The split-line match covers two consecutive added lines of one hunk. A claim spread over three or more lines, or over lines with a context line between them, is not matched.
- A cited file or commit is checked for existing, not for proving the claim. A regular file with a plausible name that says nothing cites.
- Claims in a repo whose public copy lives outside the default scope are not seen until the caller sets `claims_paths`.

The input `claims_mode` sets the mode. `enforce` is the default. `report` never fails and only writes the step summary. The input `claims_url_hosts` sets the URL allowlist, `claims_paths` the scope, and `claims_evidence_paths` the evidence path globs. The result goes to the step summary, not to a PR comment. The job checks out the script and lexicon from this repo at `job.workflow_sha`, the commit of the workflow file the caller pinned.

The job is a new check, `claims check`. Add it to branch protection only after a canary run.

## Pinning and releases

The current release is **v1.1.2** (`df7fe2a8efe50421e1e8dfb4bcc203c4b585d386`, tag `v1.1.2`). It keeps the gateway key off curl argv, and its caller example passes `AGENT_JURY_API_KEY` by name instead of `secrets: inherit` and forwards `api_url` / `model` from repo variables (#16). **v1.1.1** (`a868a09b27976af8239cd295a6937c966f6b1f91`) writes the PR title, body and file list to `GITHUB_OUTPUT` under a random per-run delimiter, so a PR body line `EOF` can no longer inject step outputs (#13). Pin callers by full commit SHA. The previous release, **v1.1.0** (`c6567996e98d6c2228262646749821ce4f6522cd`), added the error tier for request-build and parse failures (CER-2146) and was canaried on cerebral-work/vilicus#36. Pin callers to the release commit by SHA, with the tag in a trailing comment. The moving `v1` tag still points at the older `701db71` and will be retired or moved only by an explicit operator decision.

## Dependabot runs need their own key

A pull request opened by Dependabot runs with Dependabot secrets, not Actions secrets. On a private repo that runs Dependabot, the caller therefore needs `AGENT_JURY_API_KEY` stored as a **Dependabot** secret as well (`gh secret set AGENT_JURY_API_KEY --app dependabot`). Without one, the gateway answers 401 "No api key passed in", seen on reverie #1969. With a stale key you get 403 `key_model_access_denied`, seen on vilicus#32. The job log line `Secret source: Dependabot` shows which store a run used.
