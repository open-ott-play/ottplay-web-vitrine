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

This repository publishes an existing independently accepted OttPlay FOSS web
bundle; it never builds application packages. For stable, run `publish-herenow.yml`
manually from the default branch with `release_channel=stable`, an exact stable
`vX.Y.Z` player tag and `manifest_sha256` from
the independently accepted RC manifest receipt. Acceptance must have matched that
manifest byte for byte to the immutable GitHub Actions `release-evidence` ZIP and
verified the ZIP digest. Do not derive this input from a fresh mutable stable
download. Verify the run title's exact tag and full digest before approving the
protected `production` environment.

The workflow checks the raw manifest against the accepted digest before fetching
the distribution, then verifies release metadata, the source run's latest
successful attempt, Release gate and distribution SHA-256 before extraction.
It rechecks the source run before staging. A legitimate RC retry is supported;
the manifest must identify its current successful attempt. The publishing job
does not download cross-repository Actions artifacts or need another token.
Configure the production
publishing credentials described in the [README](../README.md#secrets--never-commit).

Preparation also verifies the repository-owned `static/demo/` against
`demo-media.json` and adds the MP4, HLS playlist and all HLS segments to `dist/demo/`.
The upstream player archive intentionally omits these shared media files.
Because a here.now update replaces the complete site, the publishing wrapper
rechecks the staged demo and refuses missing or changed files before publishing.
An upstream archive containing `/demo/` fails preparation and requires an
explicit reconciliation. See [demo media maintenance](demo-media.md).

Release events and `repository_dispatch` do not publish the site. In a fresh
checkout, `python3 scripts/prepare-dist.py vX.Y.Z "$ACCEPTED_MANIFEST_SHA256"` verifies and stages the selected
bundle in `dist/` without publishing.

### Explicit qualified beta deployment

When the requested delivery is beta plus site deployment, select
`release_channel=beta` and the exact already-published `vX.Y.Z-beta.N` tag. This
publishes that beta's existing bytes without creating an RC, stable tag or build.
The default remains stable; each channel rejects tags from the other channel,
and RC, nightly, alpha and noncanonical beta tags are rejected.

Before dispatch, independent local acceptance must validate the actual source,
current successful source run and attempt, exact frozen plan, source policy,
published asset inventory, web archive and build metadata. It must also download
the source run's unique immutable `release-evidence` Actions artifact, verify the
API ZIP digest and prove its manifest bytes equal the published raw manifest.
Use that accepted raw manifest SHA-256 as the workflow input, never a digest
calculated only from a fresh mutable release download. Keep the accepted raw
manifest, ZIP, web archive and receipts, and run preview/browser acceptance on the
same staged package before approving production. The production reviewer must
bind the exact beta tag and full digest in `Publish <tag> · <digest>` to those
receipts. A successful beta build alone is not deployment acceptance.

The protected job preserves this privilege boundary: it does not gain a
cross-repository Actions artifact token or download the evidence ZIP itself.
It checks the trusted digest before using the manifest, requires a published
beta prerelease and matching canonical beta plan, and compares the embedded
policy to the exact source Git blob. Qualification blockers must be empty.
The source must be a successful `main` push or manual release-pipeline run in
the upstream repository, with its current attempt and an explicit successful
Release gate. The job verifies the complete public release-asset inventory,
API digests and downloaded web SHA-256, and checks the full beta version,
source revision and player digest in `build-info.json`. It rechecks the source
run, tag, release and asset identities before staging. No version metadata or
upstream JavaScript is rewritten. The usual demo, MSX, hosted-profile, runtime
graph, Site Data and proxy guards still run.

For a staging-only check in a fresh checkout, use
`python3 scripts/prepare-dist.py "$ACCEPTED_BETA_TAG" "$ACCEPTED_BETA_MANIFEST_SHA256" beta`.
The ordinary RC/stable acceptance helpers are intentionally insufficient for
this path: their manifest and source-event rules differ. Use a separately
reviewed beta acceptance/approval receipt; never forge a stable-promotion receipt
or relabel beta evidence as RC evidence.

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

Preparation copies the reviewed `local/swop.json`, `.herenow/proxy.json`, and
`.herenow/data.json`, and derives `local/hosted.js` from its reviewed template.
It inserts that profile with a content-hash query as the first synchronous script
in `index.html`. Profile initialization must happen before any player code runs;
async or delayed configuration could select a legacy server transport. Archives
containing their own conflicting profile or publication controls fail preparation.

The derived profile selects `hosted-runtime/<graph-sha>/hosted/epg-worker.js`.
Preparation copies the exact worker, pako, SAX, runtime polyfills and shared core
from the verified release into that directory, preserving their relative import
paths. The graph hash binds every path and exact file bytes; the profile hash then
binds its selected graph. Validation recomputes both and rejects altered files,
extra graph entries, symlinks, stale URLs and unreviewed import dependencies.
Original release JavaScript remains unchanged and available at its original paths.

Before deployment, retain a separate browser acceptance context with HTTP caching
enabled and prime the previous profile, worker and imported dependencies. After
deployment, navigate normally and verify the newly selected profile and complete
worker graph against the staged hashes. Do not use request interception for this
check: it can disable the browser HTTP cache and conceal stale assets. Also verify
the entry page is revalidated and that no service worker substitutes old files.
The guarded runtime tests that block retired infrastructure remain a separate check.

The profile selects:

- Client XMLTV loading and parsing from `https://cdn.epg.one/epg2.xml.gz` in a
  Web Worker, refreshing every two hours and caching the validated guide locally.
- SWOP QR/link pairing through here.now Site Data collection `swop_pairs`.
- One fixed VPortal provider route at `POST /vportal/provider-1`.

These EPG, SWOP and VPortal features require no installation credential or
separately operated runtime. The optional home command-server bridge below has
its own discovery and pairing boundary. The validator rejects the retired routes
to `epg.2560801.xyz` and `swop.2560801.xyz`,
additional proxy routes, embedded secrets, changed upstreams and changed schemas.

## Home command-server discovery

The public hosted script also sets `window.__OTT_CONTROL_DISCOVERY_URL__` to the
exact deployment-specific URL `https://www.2560801.xyz/ott-control/api/discovery`.
The bridge resolves DNS-SD on the configured home network and returns server
metadata. It is separate from here.now Site Data and the VPortal proxy; neither
manifest gains another route or collection. No device or installation credential
belongs in this script or the discovery response.

This deployment opts unconfigured clients into one startup discovery attempt.
Generic clients without this profile have no personal home-network URL. Bridge
failure must be nonblocking, and configured clients must preserve their settings.

Require an accepted upstream candidate with
`window.__OTT_CONTROL_DISCOVERY_VERSION__ = 1`, in addition to the existing hosted
and SWOP markers. The marker is a compatibility tripwire, not a substitute for
testing the emitted player. Keep the profile synchronous and include the entire
script in its content hash; the five-file EPG graph remains derived exclusively
from the accepted upstream bytes.

Before promotion, verify the emitted player against the configured bridge:

- Discovery sends no existing command token, provider key or playlist URL and
  returns metadata only. Bridge failure leaves EPG, SWOP and playback available.
- A discovered server creates a pending pairing request. Only explicit operator
  approval with `ott approve NAME CODE` allows an individual device credential to be
  applied. Merely loading this Site or discovering an address grants no trust.
- The trusted bridge returns only controllers matching its configured
  `public_url`, including the exact HTTPS origin and base path. Reject unrelated
  DNS-SD targets rather than trusting their own claim to be approved.
- Existing control settings are preserved unless the user deliberately starts
  replacement pairing. Cancellation, expiry or changed settings cannot apply a
  stale approval. A credential from one device must not authorize another.
- Hosted-browser CORS and LG navigation work at the actual deployment origin.
  Use a controlled test device and clean up its pending pairing and credential;
  never exercise a real user's token in public acceptance logs.

Retain the usual EPG, SWOP, demo, MSX and cache acceptance checks. Publishing the
profile does not deploy the bridge or approve a pairing. Coordinate the new
source candidate, bridge readiness and publisher revision before the official
RC, stable and here.now delivery; do not republish an older stable to enable this
feature ahead of its compatible source release.

Keep core-feature regression checks isolated with saved, disabled control-server
fixture settings, so startup discovery does not contact a live operator service.
Do not weaken their retired-infrastructure guards or disable HTTP caching. Test
automatic discovery separately with a fresh unconfigured client and an exact
allowlist of the approved discovery and pairing routes, methods and origin.

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
