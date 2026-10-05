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
`vX.Y.Z` player tag, `expected_version_id` from the reviewed current production
owner inventory, and `manifest_sha256` from
the independently accepted RC manifest receipt. Acceptance must have matched that
manifest byte for byte to the immutable GitHub Actions `release-evidence` ZIP and
verified the ZIP digest. Do not derive this input from a fresh mutable stable
download. Verify the run title's exact tag, full digest and expected live version before approving the
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
publication copy described below before approving production. The production
reviewer must
bind the exact beta tag, full digest and owner version in
`Publish <tag> · <digest> · from <version>` to those
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
publisher revision. Local checks also use this checkout: the request contract
tests run the actual publisher against an offline transport, with no API key or
live site access.

Every existing-site update requires `HERENOW_EXPECTED_VERSION` (the workflow's
`expected_version_id`) and rejects `OVERWRITE`. Obtain the version together with
the current file inventory through the authenticated owner API, reconcile it
with the accepted production receipt, and retain that receipt for approval.
Do not automatically fetch and accept a newer version during publishing.

Existing-site updates preserve immutable runtime URLs used by already-open tabs.
The wrapper first runs the unchanged strict checks against the accepted stock
`dist/`. It then creates a private publication copy, reads the authenticated owner
inventory at exactly the reviewed version, and retains every existing
`hosted-runtime/<digest>/` graph. It never edits the stock stage or replaces a
new-release file. The current release still selects its normal canonical graph.

Each retained graph must contain exactly the seven reviewed runtime paths. Owner
sizes and SHA-256 hashes must match the downloaded bytes; JavaScript MIME types
must match the pinned publisher. The directory name must match the canonical graph hash or the previously deployed
sorted-path variant. Worker import rules still apply. Owner reads reject redirects
and pending publications, and recheck the version and inventory before the copy
is accepted. Any live demo, MSX, `.herenow/` or `local/swop.json` inventory, size,
hash or MIME difference from the stock stage stops publication for reconciliation.
The final copy must contain exactly the unchanged stock tree plus these attested
additions. Receipts are evidence outputs, never an input that grants exceptions.

For local browser acceptance, construct the same copy without publishing:

```sh
python3 scripts/retain_runtime.py ./dist ../publication-copy \
  --receipt ../publication-retention.json \
  --workspace ottplay --slug liminal-sketch-vv8r \
  --expected-version "$REVIEWED_PRODUCTION_VERSION_ID"
```

Use new output paths outside `dist/`; the receipt also stays outside the publication
copy. The command needs owner credentials and makes only authenticated GETs. Keep
its receipt (owner identity, graph/file hashes, exact union and final tree digest)
with browser/cache acceptance. Preview this copy and compare the complete deployed
inventory with it after publishing. The wrapper reconstructs the same copy before
its CAS update and removes its temporary copy, receipt and publisher state afterward.
The strict `swop.py --runtime` command continues to accept only the stock stage.

Retention is bounded at 16 graphs, 2 MB per graph file, 8 MB per graph and 32 MB
total. Reaching a limit fails closed; it never silently prunes an old URL. Any
future removal or changed publication controls requires a separate reviewed
reconciliation, not an overwrite flag.

The wrapper seeds a private temporary `.herenow/state.json` with only that
reviewed version, slug and absolute publication-copy path. This is the pinned
publisher's supported mechanism for putting `baseVersionId` in the update PUT.
The service atomically rejects a stale base before granting uploads and rechecks
it when finalizing. Either conflict fails the run without retry or overwrite;
reconcile the changed owner inventory before requesting a new approval. Temporary
state is removed on success or failure; caller state and credential files are
untouched. Creation of a new preview without a slug requires no base version;
subsequent preview updates require their own reviewed version.

## Hosted profile

A compatible upstream bundle contains the `hosted-profile-v1` and
`ottplay.swop.v2` runtime markers, the exact numeric
`window.__OTT_HOSTED_EPG_SERVER_VERSION__=1` capability, plus
`hosted/epg-server.js`, `hosted/epg-worker.js`, `hosted/epg-diagnostics.js`,
`swop-input/index.html`, and `swop-input/app.js`. Preparation and direct
publication reject missing files and older runtimes, even when their release
checksums are valid. These tripwires complement runtime acceptance tests.

Preparation copies the reviewed `local/swop.json`, `.herenow/proxy.json`, and
`.herenow/data.json`, and derives `local/hosted.js` from its reviewed template.
It inserts that profile with a content-hash query as the first synchronous script
in `index.html`. Profile initialization must happen before any player code runs;
async or delayed configuration could select a legacy server transport. Archives
containing their own conflicting profile or publication controls fail preparation.

The derived server profile selects `hosted-runtime/<graph-sha>/hosted/epg-server.js`
and retains the XMLTV worker URL for explicit custom feeds. Its `diagnosticsUrl`
selects the self-contained diagnostics panel under that same immutable graph;
the entry bundle loads this optional UI only when requested.
Preparation copies both exact workers, the diagnostics panel, pako, SAX, runtime polyfills and shared core
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
The runtime tests that block legacy routes and unwanted public XMLTV downloads
remain a separate check.

The profile selects:

- Server-prepared default EPG: `mode: "server"`, `sourceId: "epg-one"`,
  `apiBase: "/epg/v1"`, using only exact match/programmes routes to the dedicated
  Rust service. Explicit custom or mixed feed profiles retain local XMLTV processing.
- SWOP QR/link pairing through here.now Site Data collection `swop_pairs`.
- One fixed VPortal provider route at `POST /vportal/provider-1`.

These features need no installation credential in the public profile. EPG now
depends on our dedicated k3s Rust service and Cloudflare tunnel; SWOP and VPortal
retain their existing here.now transports. The optional home command-server bridge
below has its own discovery and pairing boundary. The validator allows only two
exact EPG v1 routes and the existing VPortal route, rejecting legacy or wildcard
routes, embedded secrets, changed upstreams and changed schemas.

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
script in its content hash; the seven-file EPG graph remains derived exclusively
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

## Publication acceptance

Inspect finalize warnings for both manifests. A successful static upload alone
does not prove proxy routes or Site Data were accepted. Preserve demo and MSX
files and verify the phone companion loads. Run browser EPG checks for РЕН ТВ HD
(current programme and archive), repeat with a warm cache, and verify TV playback
remains responsive during refresh. See [EPG acceptance](epg-service.md).

Default-source browser traffic must contain no public XMLTV downloads and no
legacy EPG endpoints. Verify generation conflicts, cold readiness and stale-cache
behavior. Keep the dedicated EPG Deployment, Service, NetworkPolicy and DNS/tunnel
routes available while server mode is live. The previous retirement procedure is
historical; do not execute it for this profile. Keep a recorded prior here.now
version and exact infrastructure/image identities for a reviewed rollback.

### Server EPG cutover using the next qualified beta

The source PR must first be merged and its official beta release must contain
both EPG workers and the numeric server capability marker. Do not synthesize a
version or substitute a pre-merge local build. After independent artifact
qualification, use a fresh publisher checkout at the reviewed merged revision:

```sh
python3 scripts/prepare-dist.py "$ACCEPTED_BETA_TAG" "$ACCEPTED_BETA_MANIFEST_SHA256" beta
python3 scripts/swop.py ./dist --runtime
python3 scripts/check-epg.py https://epg.2560801.xyz
```

These are staging/verification commands, not publication. They preserve release
bytes and fail on an old frontend, wrong proxy routes, missing worker or cold
backend. Construct the owner-verified publication copy described above and use
that exact payload and accepted backend generation for preview acceptance. Complete
the [browser/TV acceptance](epg-service.md#acceptance)
on the preview, including no default-source public XMLTV request and generation
recovery, and record the preview inventory plus backend immutable image digest.
Reconcile live owner file/version drift before the complete production replacement.

Only then dispatch the existing protected workflow from the merged default
branch, with the exact accepted tag, manifest digest and reviewed production
owner version:

```sh
gh workflow run publish-herenow.yml --repo open-ott-play/ottplay-web-vitrine --ref main \
  -f tag="$ACCEPTED_BETA_TAG" \
  -f manifest_sha256="$ACCEPTED_BETA_MANIFEST_SHA256" \
  -f expected_version_id="$REVIEWED_PRODUCTION_VERSION_ID" \
  -f release_channel=beta
```

The job preserves all existing production approval and artifact checks. Immediately
before publication it now runs `check-epg.py` against the fixed backend origin;
a cold, empty or incompatible generation fails the job before the player changes.
After deployment, run the same smoke check against
`https://player.ottplay.here.now` and complete the captured-browser acceptance.
The pre-cutover network check cannot prove here.now proxy behavior until the
preview/production routes are tested. It also does not replace LG measurement.
