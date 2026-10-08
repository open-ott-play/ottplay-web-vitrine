# Contributing

Use [GitHub issues](https://github.com/open-ott-play/ottplay-web-vitrine/issues) for non-sensitive bug reports, questions and
feature proposals. Include the exact version/commit, environment, expected and
actual behavior, and a minimal sanitized reproduction. Check existing issues
first and keep follow-up evidence in the original thread. For vulnerabilities,
use the [private security process](SECURITY.md).

Submit a focused pull request against `main`. Describe the user-visible problem,
the resulting behavior, compatibility implications and checks performed. Preserve
existing authorship and third-party license/provenance records. Discuss changes
to protocols, storage, device safety or dependency/runtime requirements before
making an incompatible change. English is the common language for code review
and project documentation.

## Development and validation

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install PyYAML==6.0.3
bash scripts/ci.sh
```

This repository packages an independently accepted player artifact; it does not compile the player. Validation and local artifact preparation do not publish a site. Follow the release-workflow document for the separately authorized publication step.

The [CI workflow](.github/workflows/validate.yml) is the authoritative list of required jobs.
Use isolated test data and temporary outputs. Never run a device write, unlock,
deployment or publication command merely to validate a documentation change.

## Test and review policy

Changes to behavior must add or update automated tests that fail for the old
defect and cover the new boundary; regression fixes should include the relevant
failure case. If automation is infeasible, explain why in the PR and document
the reproducible manual procedure and limits. Update user/API documentation and
release notes for user-visible changes. Keep compiler, lint, static-analysis and
test assertions enabled, resolve new warnings, and document any remaining
warning with its reason and scope. Do not suppress a real security finding to
obtain a passing check. Wait for required checks and independent review before
merging; do not use an administrator bypass.

## Workflow validator dependency

The workflow validator installs PyYAML from
`.github/requirements-workflow-contracts.txt` with `--require-hashes` and
`--only-binary=:all:`. When updating the version, review its PyPI release and
replace the SHA-256 wheel list for all supported Python/platform builds; do not
remove hash verification. Run `scripts/workflow_contracts.py` with the new lock.
