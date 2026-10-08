# ottplay-web-vitrine

Public **static** OttPlay FOSS web player hosted on [here.now](https://here.now).

**Live demo:** https://player.ottplay.here.now/

Upstream player builds: [`open-ott-play/ottplay-foss`](https://github.com/open-ott-play/ottplay-foss) (release asset `ottplay-foss-dist.tar.gz`).

<!-- ci-release-process:start -->
## CI and deployment

See [CI and deployment workflow](docs/release-workflow.md) for required checks and local commands. This repository uses validation-only policy; application release channels do not apply.
<!-- ci-release-process:end -->

## Why this exists

Desktop / Cap / Tauri installs are great for daily use. This repo answers:

> Can someone **try the full FOSS player in a browser** without cloning or paying for App Store / Play?

Yes — publish the static frontend to a stable here.now workspace URL and explicitly approve updates from independently accepted stable or explicitly requested qualified beta releases.

## What it is

| Layer | Role |
| --- | --- |
| Browser | Static OttPlay UI (`index.html` + `stbPlayer.js` + assets) |
| here.now workspace `ottplay` | Hosts the Site at label **`player`** → `https://player.ottplay.here.now/` |
| ottplay-foss releases | Source of truth for the built web bundle |

```text
accepted ottplay-foss release  →  verify and unpack ottplay-foss-dist.tar.gz
                           →  add checksum-pinned static/demo/ media
                           →  here.now publish (workspace ottplay)
                           →  https://player.ottplay.here.now/
```

## Limits (static host)

- **No** `ottplay-server` / HLS proxy / command-queue on here.now.
- Playlists and video streams must be endpoints the browser can reach (HTTPS and CORS permitting).
- Default M3U EPG is prepared by our dedicated Rust service on k3s and fetched
  through two fixed here.now routes. Explicit custom XMLTV sources remain local
  to the device; see [EPG operation](docs/epg-service.md).
- SWOP text entry uses encrypted pairing records in here.now Site Data. VPortal uses
  a fixed here.now proxy directly to the approved provider. These player features
  require no separately operated k3s service, private Cloudflare Worker or tunnel.
- Optional home command-server discovery uses the deployment-specific HTTPS
  bridge described below. It does not supply a command credential or grant control.

## Shared demo media

`static/demo/` owns the synthetic MP4, HLS playlist and all 12 HLS segments used
by **Try demo** across the web and native players. These files are intentionally
absent from upstream application bundles. Every prepared distribution includes
them under `/demo/`, preserving the existing URLs when here.now replaces the
whole site during a stable deployment.

`demo-media.json` pins their sizes and SHA-256 hashes. Preparation, local CI and
the publishing wrapper verify the complete demo; missing or changed files stop
the deployment. See [demo media maintenance](docs/demo-media.md) for provenance
and update instructions.

## How to update the live site

Every prepared publication also includes the repository-owned `static/msx/`
bootstrap. In Media Station X, choose **Settings → Start Parameter → Setup**,
enter `player.ottplay.here.now`, enable the HTTPS lock and confirm **OTT-play
FOSS**. MSX reads `/msx/start.json`, then `/msx/content.json`, and opens the player.
The displayed bootstrap version is separate from the player release version.
If startup is interrupted with the MSX menu button, an **Open OTT-play FOSS**
tile remains available. The publishing wrapper rejects missing or modified
bootstrap files before uploading, just as it checks the shared demo media.
The host must serve both JSON files as `application/json` with CORS enabled.

Every publication derives `local/hosted.js` from the reviewed
`static/local/hosted.js` template and inserts its synchronous script before the
upstream HTML's other scripts. The script URL includes its content hash. This hosted profile
selects server-prepared EPG for the default public source, local XMLTV processing
for custom or mixed sources, here.now Site Data pairing and an exact VPortal
provider route. `static/local/swop.json` is deliberately empty: this installation no
longer selects the old Worker relay. Local and native player installations keep
their separate transport configuration.

### Home command-server discovery

The same synchronous profile sets `window.__OTT_CONTROL_DISCOVERY_URL__` to
`https://www.2560801.xyz/ott-control/api/discovery`. This public endpoint returns
DNS-SD server metadata from the configured home network. A hosted browser cannot
query that network's DNS directly; native and local-server clients use their own
system DNS through their discovery implementation.

With this deployment profile, a compatible player attempts discovery at startup
when no command server is configured. Existing settings are preserved. A missing
or unavailable bridge does not block the player; the user can retry discovery
from command-server settings.

Discovery is an optional control feature backed by the separately operated home
bridge. Playback, EPG and SWOP do not depend on that bridge. Finding an address is
not permission to control the player: pairing requires explicit approval with
`ott approve NAME CODE` and issues an individual device credential. Existing command
credentials, provider passwords and playlist URLs must not be sent to discovery
or included in the public profile. See the [control discovery acceptance checks](docs/release-workflow.md#home-command-server-discovery).

The publisher requires the upstream `window.__OTT_CONTROL_DISCOVERY_VERSION__ = 1`
capability marker before enabling this profile. Do not deploy an older player
with the new profile or infer compatibility from a release tag alone.

### Hosted EPG and pairing assets

The EPG server worker, XMLTV worker, optional diagnostics panel and four XMLTV
dependencies form a seven-file graph copied byte for byte from the verified
release into `hosted-runtime/<graph-sha>/`, preserving their relative
paths. The profile selects the server worker by default and retains the XMLTV
worker for explicit custom sources, so a change to any dependency gives
the entire worker graph a new address. A query on the worker alone would leave
its `importScripts` dependencies cached under their old URLs. The original release
files remain available; no release JavaScript is rewritten or rebuilt. Publication
recomputes the hashes and rejects missing, modified or unexpected graph files.
Reload the player after deployment to load the new entry page and profile.

The repository-owned `static/.herenow/data.json` declares `swop_pairs`. SWOP's
**♥™** action creates a pairing record and displays a QR/link carrying a secret
in the URL fragment. Phone and player exchange authenticated encrypted payloads;
keys, drafts and provider passwords must never appear in Site Data as plaintext.
Same-origin access identifies the installation, while the pairing secret binds
the two devices. Collection reads and mutations are public, so the client must
reject tampered, expired, wrong-pair and already consumed messages. The record
is deleted on completion or cancellation; deletion/quota behavior must be
checked on here.now. See the [hosted acceptance checks](docs/release-workflow.md#hosted-profile).

`static/.herenow/proxy.json` contains exact `POST /epg/v1/match` and
`GET /epg/v1/programmes` routes to the dedicated Rust EPG service, plus the
existing exact `POST /vportal/provider-1` route to the approved VPortal API. The hosted client sends the provider's
JSON body directly through that route. Unknown provider URLs fail locally;
there is no request-supplied upstream or wildcard VPortal proxy. User subscription
keys remain in their settings and POST body, never the checked-in profile or
manifest. VPortal carries catalog JSON, not video streams. Include this manifest
on every publication: omitted proxy manifests remove routes from the next version.

Legacy EPG/SWOP routes and wildcard proxies remain rejected by the publication
validator. The new EPG API accepts channel metadata and a fixed source ID, never
private feed URLs. The former installation variable is not referenced. The Rust
EPG service, its readiness and immutable image pin must be accepted before the
server-mode frontend is published; see the [EPG runbook](docs/epg-service.md).
The [beta.16 production acceptance record](docs/epg-rollout-2026-09-30.md)
documents the deployed architecture, exact release identity, measured results
and checks that still require a physical LG/MSX device.

### Publish a reviewed stable release

Select a release with `hosted-profile-v1`, encrypted Site Data SWOP and the
server EPG capability marker `__OTT_HOSTED_EPG_SERVER_VERSION__=1`, both EPG
workers and phone companion. Preparation and publication reject
older bundles or missing assets before they can replace the live player. The
marker is a compatibility tripwire; release checks and browser/TV acceptance
remain required.

1. Configure `HERENOW_API_KEY` in the protected production environment with access to the `ottplay` workspace. Review the workspace and site slug configured in the workflow.
2. Run **Actions → Publish here.now → Run workflow** from the default branch with an exact stable `vX.Y.Z` tag from `open-ott-play/ottplay-foss`.
3. Supply `manifest_sha256` from the independently verified, accepted RC manifest receipt. That acceptance must verify the manifest against the immutable GitHub Actions `release-evidence` artifact and its ZIP digest. Do not calculate this input from the current mutable stable download.
4. Check that the deployment run title contains the exact stable tag and full accepted manifest SHA-256, then approve its production environment. The workflow compares the downloaded manifest bytes with that digest before downloading the web archive, and verifies the archive, source revision and latest successful validation attempt before publishing. It uses public release downloads without requiring a cross-repository Actions artifact token.

Release events and `repository_dispatch` do not publish the site. See the [operator runbook](docs/release-workflow.md) for the repository validation and deployment boundary.

### Prepare verified artifacts locally

In a fresh checkout with no existing `dist/`, run `python3 scripts/prepare-dist.py vX.Y.Z "$ACCEPTED_MANIFEST_SHA256"` for the selected stable player release, using the digest from the immutable-verified accepted RC receipt described above. This verifies and stages the distribution without publishing. Inspect `dist/`; use the protected workflow above to update the live site.

### Publish an explicitly requested qualified beta

Use the same protected workflow with `release_channel=beta`, an exact published
`vX.Y.Z-beta.N` tag and the raw manifest SHA-256 from independent beta acceptance.
That acceptance must authenticate the immutable Actions evidence ZIP and the
published manifest/archive before preview/browser acceptance and production
approval. Check the exact tag and full digest in the run title against the saved
receipt. The default channel remains stable and mismatched channel/tag inputs
fail closed. The beta path verifies the frozen plan, source policy, successful
current `main` release run, complete API asset inventory and full beta build
metadata, preserving the downloaded player bytes without RC/stable promotion or
rebuild. See [the beta deployment contract](docs/release-workflow.md#explicit-qualified-beta-deployment).

Discover the Site slug (owner API key):

```bash
curl -sS -H "Authorization: Bearer $HERENOW_API_KEY" \
  -H "X-HereNow-Account: ottplay" \
  https://here.now/api/v1/publishes | jq .
```

Keep the **`player`** workspace label pointed at that Site (here.now dashboard / site-labels API). Updating the Site by `--slug` refreshes `https://player.ottplay.here.now/` without renaming the label.

## Secrets — never commit

| Name | Purpose |
| --- | --- |
| `HERENOW_API_KEY` | Authenticated publish into workspace `ottplay` |
| `HERENOW_SITE_SLUG` | Existing Site slug to `PUT` (update) instead of creating a new Site |
| `HERENOW_WORKSPACE` | Defaults to `ottplay` |

Do not commit `~/.herenow/credentials`, `.herenow/state.json`, user playlist or
provider keys. The hosted profile and manifests contain no installation secrets.

## Related

- Player source / releases: https://github.com/open-ott-play/ottplay-foss
- Privacy policy: https://github.com/open-ott-play/ottplay-foss/blob/main/docs/privacy-policy.md
- here.now docs: https://here.now/docs

## License

MIT

## Project maintenance

See [contribution and test requirements](CONTRIBUTING.md), the
[security reporting policy](SECURITY.md), [security design](docs/security-design.md),
and the [OpenSSF evidence and remaining criteria](docs/openssf-evidence.md).
