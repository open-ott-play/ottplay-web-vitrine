# Public EPG production rollout — 2026-09-30 UTC

This is the acceptance record for the `v1.1.51-beta.16` cutover at
[player.ottplay.here.now](https://player.ottplay.here.now/). It records that
deployment, rather than asserting which release is live after later updates.
Production publication completed at `2026-09-30T00:03:12.933Z`.

## Architecture and ownership

LG/MSX previously downloaded and processed the complete public XMLTV feed. The
default public source now uses a dedicated Rust service on ARM64 node `h7` in
`k3s-heaven`, namespace `synology-apps`. It downloads, decompresses, parses and
indexes the feed once for all clients, with normal refreshes every two hours.
Clients request channel mappings and only the programme window they need.

here.now hosts the static player, MSX bootstrap, demo media and immutable runtime
assets. It forwards exactly `POST /epg/v1/match` and `GET /epg/v1/programmes`
through `epg.2560801.xyz` and the existing Cloudflare h7 tunnel to
`Service/ottplay-epg:8080`. here.now does not run the Rust service or schedule
XMLTV processing. The fixed VPortal proxy and encrypted SWOP Site Data retain
their separate here.now transports; video does not pass through the EPG service.

Only the canonical default public source uses the server. Explicit custom or
mixed feeds retain local XMLTV processing and source precedence. Requests carry
channel metadata and a fixed source ID, never private feed URLs, playlist URLs,
stream addresses or credentials. Server failures preserve usable cached guide
data and expose an error; they do not silently start a full XMLTV download on
the TV. The server deadline remains eight seconds, including queue time, with
two admitted requests and one executing match; the client request timeout is
12 seconds. See the [operating runbook](epg-service.md) for routes, diagnostics,
generation handling and the deployment procedure.

The service moved from `mp` to `h7` after the `mp` candidate failed the bounded
large-playlist check and the h7 candidate passed it. `mp` was identified as a
VirtualBox guest; the tests did not establish a particular virtualization
mechanism as the cause. The accepted rollout required no cluster-wide networking
or virtual machine changes. Shared-core trie matching avoids repeated substring
allocations while preserving matching precedence, shifts and compatibility.

## Release and change identity

- Player release: [v1.1.51-beta.16](https://github.com/open-ott-play/ottplay-foss/releases/tag/v1.1.51-beta.16),
  source `f4644eead78de20530c5efede6101cdea98f8db8`.
- [Official release workflow 36645148200](https://github.com/open-ott-play/ottplay-foss/actions/runs/36645148200)
  succeeded; all 27 published asset digests, immutable workflow evidence, both
  OCI platforms and all 42 OCI blobs were verified.
- Accepted release manifest SHA-256:
  `18a7880e94a78f6a7be57694829fec48fb03255815f6415c93e69d3f2397d8b7`.
- Deployed ARM64 image manifest:
  `sha256:fd8e93bbcd173b6fd763710b450cf5ffe1f2ee00f03febe52afe1e8758582aae`;
  config `sha256:cd8f8768f20d4490528c23757cd597079568c1c218c303a3635a29f280642825`.
  The private registry's raw manifest and digest header matched the accepted
  official bytes before deployment.
- Vitrine merge: `03b461a2893ae5867343545e6a3a77511c11b031`, byte-identical tree to
  the tested `b6f4f5a780a6d0ae5849b8b48e23459f80e9b6ca` branch.
- [Protected production workflow 36648261958](https://github.com/open-ott-play/ottplay-web-vitrine/actions/runs/36648261958)
  succeeded after preview acceptance and a fresh owner-version/inventory check.
- Production here.now version: `01M3QSXKPXGQRF0875XAQQTNSC`, 234 files.

Implementation is recorded in [FOSS #618](https://github.com/open-ott-play/ottplay-foss/pull/618)
(server API, client transport, cache and diagnostics),
[FOSS #619](https://github.com/open-ott-play/ottplay-foss/pull/619)
(bounded matching and cancellation),
[core #31](https://github.com/open-ott-play/ottplay-core/pull/31) and
[core #32](https://github.com/open-ott-play/ottplay-core/pull/32)
(indexed matching and the final trie),
[FOSS #622](https://github.com/open-ott-play/ottplay-foss/pull/622)
(trie integration and phase diagnostics), and
[vitrine #27](https://github.com/open-ott-play/ottplay-web-vitrine/pull/27)
(profile, routes, immutable assets and deployment).
The separate infrastructure change added the dedicated hostname and fixed
ingress while preserving all seven existing routes. The operator retains the
infrastructure approval and apply evidence. The application PRs listed above
were merged before production acceptance.

## Observed acceptance results

- Repository validation: 83 vitrine tests, final CI, Kubernetes server dry-run
  and independent review passed; Trivy reported no HIGH/CRITICAL findings.
- Official h7 container: readiness changed from 503 to 200 within **14.767 s**
  of container start. Maximum observed health/API latency during that check was
  **111.338 ms**. Cgroup-v1 peak was **1,030,778,880 bytes (983.03 MiB)** within
  the 2 GiB limit, with zero OOM/limit events or container restarts.
- Cold processing phases: download 7,331 ms; decompression 1,582 ms; parse
  2,292 ms; index 2,729 ms. The feed contained 3,247 channels, 7,405 aliases and
  680,136 programmes. Cold preparation occurs on the service, not per TV.
- One 2,048-channel HTTP request finished in **1,428.137 ms**, with the expected
  32 REN mappings, with 2,016 unmatched channels omitted. The same 431,017-byte metadata
  fixture had SHA-256
  `887502bddd14b7e14c624d54cca0d3a4d48db94507272e14ec384aae32f157f5`.
- Preview and production inventories matched all 234 staged files, including
  14 demo media files, two MSX files, the exact proxy manifest and seven runtime
  assets. Preview finalization had no warnings.
- Production Chromium EPG bridge: first REN TV HD rows in **363.3 ms** with
  fresh storage; **4.2 ms** after a normal reload using its persistent guide
  cache. These intervals start when the EPG bridge opens after page boot.
  They do not measure full application startup or physical LG performance.
- The runtime check validated 197 programme rows for REN and REN +3, descriptions,
  ordered archive rows, the three-hour shift and the accepted generation. Warm
  reload completed one successful match request and required no programme GET.
  The separate production smoke check verified a current programme and 42
  archive rows in its shorter window. There were no XMLTV downloads, local
  XMLTV-worker starts, direct backend-origin requests or browser errors.
- A retained Chromium profile primed with beta.9, without cache clearing,
  interception or cache disabling, selected the new profile/player and verified
  all seven new graph assets on ordinary navigation. This static transition
  check did not execute workers; runtime was covered by the separate EPG test.
- Independent final review passed. The temporary preview was deleted only after
  production acceptance, and its owner API returned 404. Production remained
  available; the previous publication was retained in here.now version history.

## Verification boundaries and follow-up

These checks do not establish physical LG/MSX startup time, Magic Remote pointer
behaviour, actual guide-menu navigation or archive playback after resume. The
browser diagnostics check covered cached reopening, not every loading/failure
and Back-button scenario on a television. A normal same-container two-hour
refresh was not observed during this acceptance; initial cold-load memory is
not a measurement of refresh overlap. Keep these items separate from the
successful production rollout and use the [runbook acceptance checks](epg-service.md#acceptance)
for follow-up.

For LG/MSX, reopen the player to load the new entry page and hosted profile.
The retained-cache test passed without clearing saved settings. If a custom or
mixed XMLTV source is selected, local feed processing is expected and the
server-mode timings above do not apply.
