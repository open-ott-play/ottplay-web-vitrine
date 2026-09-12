# CI and release policy

This repository has a validation-only policy. The `Quality gate` workflow runs
on pull requests, merge queue entries, the default branch, and a staggered
nightly UTC schedule. Every configured validation workflow must finish
successfully; a skipped or failed workflow does not pass `CI gate`.

Install actionlint 1.7.12 (and Node.js when JavaScript sources are present).
Run the same local checks:

```sh
python3 -m pip install PyYAML==6.0.3
bash scripts/ci.sh
```

Request or inspect CI from a local checkout:

```sh
gh workflow run quality-gate.yml
gh run list --workflow quality-gate.yml
```

No beta, RC or stable application release is synthesized from configuration or
reference source. Disabled legacy publisher entry points only explain this
migration. Their exact previous contents remain in `docs/legacy-workflows/`.
Production deployment, where provided, requires manual dispatch from the default
branch and the `production` environment; validation never deploys resources.

`publish-herenow.yml` accepts only an explicit stable `vX.Y.Z` player tag. It checks the stable metadata, source run and Release gate, and verifies distribution SHA-256 before extraction. Configure the production environment and publishing credentials; publishing errors fail the job.

## Coverage limits

- Validation-only policy: no synthetic beta/RC artifacts or tag-triggered stable releases.
- Syntax baseline only: YAML/JSON/Python/shell/JavaScript where present. No application tests, browser playback, cluster rendering or deployment checks implied.

The publisher is pinned to `heredotnow/skill@8cf033ed53b82c0c67b16359c8c431f99e111d04`; CI no longer executes a mutable installer. For manual local publishing, clone that repository into `.ci-tools/herenow`, check out this exact commit, then use `scripts/publish-herenow.sh`. The wrapper rejects a different or modified publisher revision. No publication was performed during migration validation.
