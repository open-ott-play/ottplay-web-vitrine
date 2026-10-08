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
python -m pip install --require-hashes --only-binary=:all: -r .github/requirements-workflow-contracts.txt
git clone --filter=blob:none --no-checkout https://github.com/heredotnow/skill.git .ci-tools/herenow
git -C .ci-tools/herenow checkout --detach 8cf033ed53b82c0c67b16359c8c431f99e111d04
bash scripts/ci.sh
```

The pinned publisher checkout is used by offline request-contract tests and
loopback TLS tests; neither publishes a site. The TLS tests need OpenSSL and an
OpenSSL 3 curl, selectable with `PUBLISH_TLS_TEST_CURL=/path/to/curl` on hosts
whose default curl uses another backend. CI runs them on Ubuntu with OpenSSL 3.
For an existing checkout, verify its exact revision rather than cloning over it.

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

## Source releases

Use immutable Semantic Versioning tags (`vMAJOR.MINOR.PATCH`) for source snapshots.
The first source release is `v0.1.0`; do not move an existing release tag.
These versions identify the preparation/publication tooling, static support assets, tests and documentation. They are independent of ottplay-foss player versions and do not contain a newly built player or publish the live site. The validation-only workflow policy and separately authorized deployment process remain applicable.

Before tagging, identify the exact reviewed commit and verify its required CI
checks. Each release must link that commit and describe changes, upgrade
implications and security impact, including known limits. GitHub source archives
allow users to obtain the exact tagged tree; preserve all bundled licenses and
upstream notices. Report defects against the source tag or full commit ID.
