# Web vitrine documentation

Reference material for the public static OttPlay FOSS web player hosted on
here.now, including the preparation and publication tooling around it.

## The hosted site

- [Server-prepared M3U programme guide](epg-service.md) — the hosted `mode: "server"` profile, the `epg-one` source and its provisioning.
- [Public EPG production rollout — 2026-09-30 UTC](epg-rollout-2026-09-30.md) — the acceptance record for the `v1.1.51-beta.16` cutover.
- [Browser acceptance of the staged website](browser-acceptance.md) — what the staged site contains and the browser run that precedes publication.
- [Shared demo media](demo-media.md) — the synthetic test pattern, its provenance record and hashes.
- [Home command-server discovery runbook](home-control-discovery.md) — connecting a player, updating the public catalog and rolling a change back.
- [Install an isolated hosted Stalker relay](stalker-relay.md) — the explicitly configured Stalker routes served by an `ottplay-server` image.

## Security and quality

- [Security design and verification](security-design.md) — trust boundaries for release archives, downloaded media, credentials and publication.
- [Documentation images](images/README.md) — the upstream Studio 2026 screenshot reused by the hero and its provenance.

## Archive

- [Archived documentation](archive/README.md) — files kept for history that must not be run against the current deployment.
- [Historical retirement of the dedicated EPG backend](archive/epg-retirement.md) — the prior client-only cutover, superseded by the current server-mode profile.
