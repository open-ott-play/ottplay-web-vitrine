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

Preparation also verifies the repository-owned `static/demo/` against
`demo-media.json` and adds the MP4, HLS playlist and all HLS segments to `dist/demo/`.
The upstream player archive intentionally omits these shared media files.
Because a here.now update replaces the complete site, the publishing wrapper
rechecks the staged demo and refuses missing or changed files before publishing.
An upstream archive containing `/demo/` fails preparation and requires an
explicit reconciliation. See [demo media maintenance](demo-media.md).

Release events and `repository_dispatch` do not publish the site. In a fresh
checkout, `python3 scripts/prepare-dist.py vX.Y.Z` verifies and stages the selected
bundle in `dist/` without publishing.

The publisher is pinned to
`heredotnow/skill@8cf033ed53b82c0c67b16359c8c431f99e111d04`. For local publishing,
clone that repository into `.ci-tools/herenow`, check out this exact commit and
use `scripts/publish-herenow.sh`. The wrapper rejects a different or modified
publisher revision.

## SWOP relay

Choose a stable player release that implements the installation relay's
`sessionToken` protocol. Older Device ID allowlist builds are incompatible even
if their release checksums and provenance are valid. Preparation and direct
publication both reject a missing `player.js`, a bundle without the
`sessionToken` marker, or one still containing `Allowlist this Device ID`.
This compatibility guard complements the release and live runtime checks; it
does not replace them.

Preparation also stages `static/local/swop.json` and
`static/.herenow/proxy.json`. The client selects `/swop`; only the exact POST
session-creation and value-polling routes are forwarded to `swop.2560801.xyz`.
The publishing wrapper checks both files before calling here.now, including
their methods, upstreams, rate limits and server-side variable reference.
An upstream archive containing its own `local/swop.json` or `.herenow/`
publication controls is rejected and must be reconciled explicitly.

Provision `OTTPLAY_SWOP_INSTALLATION_TOKEN` with the here.now variables API in
workspace `ottplay` (`X-HereNow-Account: ottplay`), pinning `allowedUpstreams` to
`swop.2560801.xyz`. Register the corresponding installation credential and
allowed player origins in SWOP. This here.now credential uses the explicit
`originPolicy: "trusted-proxy"` transport: here.now enforces browser-origin
checks and strips Origin, Referer and custom browser headers upstream. Other
installation credentials retain `require-origin`. Do not inject a constant
Origin header to pretend that browser provenance was preserved. Client identity
and the per-session read capability travel in the JSON body.

Use an installation credential, never the SWOP administrator token. Neither
the public configuration nor release assets may contain its value. The browser
keeps its own client identity; it does not need to be individually allowlisted.

The creation route allows 60 requests/hour/IP; polling allows 7200/hour/IP.
These are shared by clients behind one NAT. The polling budget supports five
continuously active clients at the player's 2.5-second interval; text entry
normally uses a shorter fraction of the hour. Reassess both limits for a larger shared network
and adjust the manifest and its validator together. Do not rely on here.now's
default 100/hour/IP for polling.

After publishing, inspect the finalize response for proxy-manifest warnings
and test creation, form submission and one-time polling through the public
player URL. An invalid proxy manifest can leave the static site live while
silently disabling its routes. The currently pinned publishing helper does not
surface these warnings, so successful static-file publication alone is not a
SWOP acceptance check. Also verify foreign browser origins and direct SWOP
requests without installation credentials are rejected, and confirm the public
`/.herenow/proxy.json` is not served. Include the manifest on every subsequent
publication: here.now does not retain omitted routes.

Server-side injection prevents exposing a reusable installation secret to the
browser. Origin checks restrict browser copies, but a public relay cannot prove
that a non-browser caller is running the original player: another server can
relay requests through the public installation. Short-lived session
capabilities and rate limits bound that exposure; absolute prevention requires
additional trusted identity.
