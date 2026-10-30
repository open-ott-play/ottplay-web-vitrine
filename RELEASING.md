# Source releases and site publication

This repository's own source releases identify the original preparation/publication tooling, support assets, tests and documentation. They do not rename, rebuild or relicense upstream player binaries. Use immutable `vMAJOR.MINOR.PATCH` source tags, an exact reviewed commit, successful required CI, and release notes describing changes, compatibility and security impact. See [CONTRIBUTING.md](CONTRIBUTING.md#source-releases) for the existing source-release policy.

The separately authorized site publication flow is [docs/release-workflow.md](docs/release-workflow.md). It requires verified player provenance and the exact browser-qualified stage, then an explicit operator decision. Local validation never implies permission to replace a live site. Keep the accepted current owner version for compare-and-swap updates.
