# CI and deployment

The validation policy is defined in `.release-policy.json`. Documentation-only
changes skip application checks. Source, configuration and workflow changes run
`.github/workflows/validate.yml` and
`.github/workflows/workflow-validation.yml`; missing or failed required checks
fail `CI gate`. Manual and scheduled validation run the full configured checks.

## Local checks

Use Python 3.11+, actionlint 1.7.12 and Node.js for JavaScript validation:

```sh
python3 -m pip install PyYAML==6.0.3
bash scripts/ci.sh
python3 scripts/release.py status
```

Request or inspect hosted validation:

```sh
gh workflow run quality-gate.yml
gh run list --workflow quality-gate.yml
```

These checks cover the repository's syntax and workflow contracts. Browser
playback and production deployment are separate checks.

## Publishing the player

This repository publishes an existing stable OttPlay FOSS web bundle; it does not
build application beta or RC packages. Run `publish-herenow.yml` manually from the
default branch with an exact stable `vX.Y.Z` player tag and approve the protected
`production` environment. The workflow checks release metadata, the source run,
Release gate and distribution SHA-256 before extraction. Configure the production
publishing credentials described in the [README](../README.md#secrets--never-commit).

Release events and `repository_dispatch` do not publish the site. In a fresh
checkout, `python3 scripts/prepare-dist.py vX.Y.Z` verifies and stages the selected
bundle in `dist/` without publishing.

The publisher is pinned to
`heredotnow/skill@8cf033ed53b82c0c67b16359c8c431f99e111d04`. For local publishing,
clone that repository into `.ci-tools/herenow`, check out this exact commit and
use `scripts/publish-herenow.sh`. The wrapper rejects a different or modified
publisher revision.
