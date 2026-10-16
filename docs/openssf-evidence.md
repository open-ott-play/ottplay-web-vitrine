# OpenSSF Best Practices evidence

This evidence index supports the project's own Passing and Silver self-assessments. The public badge service is authoritative for awarded status. A planned or partially completed criterion is not an award.

## Project, participation and releases

Original Python/Shell preparation and publication tools, browser support assets and integration definitions. Separately released upstream player binaries retain their own identity; this assessment does not award a badge to them.

The [README](../README.md) explains installation and interfaces. [CONTRIBUTING](../CONTRIBUTING.md) gives the issue/PR process, standards, test commands and review policy. [GOVERNANCE](../GOVERNANCE.md), [Code of Conduct](../CODE_OF_CONDUCT.md) and [ROADMAP](../ROADMAP.md) record roles, private conduct reporting, priorities and the missing backup-maintainer arrangement. Source is public Git under [MIT](../LICENSE), with third-party notices preserved.

The latest reviewed stable source/release is [v0.1.0](https://github.com/open-ott-play/ottplay-web-vitrine/releases/tag/v0.1.0); its human-readable notes describe changes, upgrade implications, security scope and exact source. [RELEASING](../RELEASING.md) and [CHANGELOG](../CHANGELOG.md) define future release expectations. This work does not deploy a device or publish a new stable release.

## Testing and analysis

The standard suite exercises archive safety, provenance, immutable graph retention, compare-and-swap publication and synthetic network responses. New real TLS tests cover both Python readers and a complete three-stage publisher transaction at security level 3. The native Worker runtime also tests the VPC binding boundaries. See the current pull-request checks for exact revision results.

The [test sources](../tests), [CI workflows](../.github/workflows), [quality limits](quality.md) and [security policy](../SECURITY.md) retain the exact commands and scope. Automated tests execute with assertions. Required analysis includes CodeQL on the maintained languages; practical language/syntax checks remain enabled. New confirmed findings must be fixed or narrowly explained, never hidden by disabling a gate.

No dedicated mutation fuzzer is currently claimed for this tooling project. Real network/regression tests are not described as fuzzing.

## Security and human evidence

[The security design/profile](../docs/security-design.md) documents active trust boundaries, default behavior and the complete supported strong-key configuration. Crypto claims apply to that configuration; they do not certify arbitrary third-party infrastructure or deployment settings.

On 16 October 2026 the authenticated issue/advisory inventory contained no non-PR issues or advisories, and Dependabot contained no open alerts. Visible code-scanning findings were Scorecard process findings; an empty advisory inventory is not a claim that no vulnerability can exist. Private GitHub reporting was enabled. The available Git history scan found no credential matches.

The maintainer's secure-design/common-vulnerability knowledge and no outside-GitHub reports in the preceding 12 months are recorded in [quality limits](quality.md). These are human statements; they are not inferred from test results.

## Honest Silver progress

No backup person or tested one-week recovery arrangement exists, so access continuity and bus factor remain unmet. Complete cross-language statement coverage, systematic accessibility/internationalization, independent penetration testing and a full assurance case are not established. See [quality limits](quality.md); individual criteria remain separately assessed.
