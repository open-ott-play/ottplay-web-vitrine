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

## Hosted profile

A compatible upstream bundle contains the `hosted-profile-v1` and
`ottplay.swop.v2` runtime markers plus `hosted/epg-worker.js`,
`swop-input/index.html`, and `swop-input/app.js`. Preparation and direct
publication reject missing files and older runtimes, even when their release
checksums are valid. These tripwires complement runtime acceptance tests.

Preparation stages the exact repository-owned `local/hosted.js`,
`local/swop.json`, `.herenow/proxy.json`, and `.herenow/data.json`. It inserts
`<script src="/local/hosted.js"></script>` as the first synchronous script in
`index.html`. Profile initialization must happen before any player code runs;
async or delayed configuration could select a legacy server transport. Archives
containing their own conflicting profile or publication controls fail preparation.

The profile selects:

- Client XMLTV loading and parsing from `https://cdn.epg.one/epg2.xml.gz` in a
  Web Worker, refreshing every two hours and caching the validated guide locally.
- SWOP QR/link pairing through here.now Site Data collection `swop_pairs`.
- One fixed VPortal provider route at `POST /vportal/provider-1`.

No installation credential or separately operated runtime is required. The
validator rejects the retired routes to `epg.2560801.xyz` and `swop.2560801.xyz`,
additional proxy routes, embedded secrets, changed upstreams and changed schemas.

## SWOP pairing

The Site Data manifest allows public CRUD only on the reviewed bounded schema.
The TV creates a record and the phone/TV address that returned ID directly. A
pair-specific secret in the QR/link fragment protects authenticated encrypted
offer and reply envelopes. There is no shared plaintext secret in a public
record. Public access to the same Site is not proof of membership in one pair.
The client validates message direction, expiry and pair/record binding and
consumes only once locally. It deletes completed/cancelled records best effort.
This protocol does not promise a server-side atomic consume or native record TTL.

The manifest sets `1800/hour/ip`, allowing five-second polling and session
mutations with headroom for a few active devices behind one NAT. Limits are
approximate; handle HTTP 429 and do not keep polling after cancellation or expiry.
Measure the actual public API and validate deletion behavior before promotion;
provider documentation does not guarantee whether soft deletion reclaims the
record quota. Never store user input or pairing keys in Site Data unencrypted.

Acceptance includes opening the QR on another device, receiving the current
editor draft, submitting text, confirming it is applied exactly once and
cancelling without changing the draft. Test two concurrent pairs, tampered
messages, wrong keys, expired messages, retransmission and a missing record.
All browser calls must target this Site's `/.herenow/data/swop_pairs` paths,
without contacting the old SWOP Worker. Test old LG cryptographic support.

## VPortal route

The approved fixed upstream is `http://cd3c21307c36.vportalu.net/api/v1/`.
The published manifest pins method POST and JSON headers; the hosted client
matches the configured URL exactly, then sends provider params directly,
including `app: "ott-play"` and the user's key in the JSON body. Unknown URLs
fail before a network request. Native clients still use their direct transport;
local server clients retain the existing `/vportal/api` envelope.

The here.now route allows 1200 requests/hour/IP. Validate a root catalog and a
category with an authorized account, including content type, status handling and
provider redirect behavior. The previous Worker performed additional response
size/timeout/redirect enforcement; here.now's transparent proxy does not document
identical controls. Do not claim that the old Worker policy survives unchanged.
Errors in the UI must not display request keys or upstream error bodies.

## Publication acceptance and retirement

Inspect finalize warnings for both manifests. A successful static upload alone
does not prove proxy routes or Site Data were accepted. Preserve demo and MSX
files and verify the phone companion loads. Run browser EPG checks for РЕН ТВ HD
(current programme and archive), repeat with a warm cache, and verify TV playback
remains responsive during refresh. See [EPG acceptance](epg-service.md).

Only after the new production runtime passes those checks remove the dedicated
EPG Deployment, Service and NetworkPolicy and their DNS/tunnel entries. Do not
remove unrelated services or the whole h7 tunnel. Retire the SWOP Worker only
after confirming no other installations still use it; removing this site's
routes already eliminates its dependency on that Worker. Keep a recorded prior
here.now version and infrastructure manifests for a reviewed rollback.
