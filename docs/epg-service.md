# Server-prepared M3U programme guide on here.now

For the accepted beta.16 cutover, see the dated
[production rollout record](epg-rollout-2026-09-30.md). It separates the official
server and browser measurements from pending physical LG/MSX verification.

The reviewed hosted profile selects `mode: "server"`, `sourceId: "epg-one"` and
same-origin `apiBase: "/epg/v1"`. A dedicated Rust EPG service downloads and
indexes the public `https://cdn.epg.one/epg2.xml.gz` source once for all clients,
refreshing every two hours. The LG no longer downloads or parses that full feed
for the default public source. It receives channel mappings and only the guide
window it needs. here.now serves the player and forwards two fixed API routes;
it does not execute our Rust process or schedule XMLTV processing.

The service is external infrastructure: `Deployment/ottplay-epg` and
`Service/ottplay-epg` in `synology-apps` on the ARM64 node `h7` in `k3s-heaven`.
Cloudflare's h7 tunnel
connects `epg.2560801.xyz` to the service. DNS and tunnel ingress have a separate infrastructure owner; publication of
this repository alone does not provision them. Coordinate their changes through
the operator's infrastructure runbook. The narrow `NetworkPolicy/ottplay-epg-egress` in
`cloudflared` permits the connector to reach that service. SWOP remains here.now
Site Data, VPortal retains its fixed provider proxy, and command discovery is a
separate optional bridge. None of these routes forwards video through EPG.

## Profile and publication boundary

`local/hosted.js` sets `serverWorkerUrl` to the immutable
`/hosted-runtime/<graph-sha>/hosted/epg-server.js`. The existing XMLTV worker,
pako, SAX, polyfills and shared core stay in that same content-addressed graph.
`diagnosticsUrl` selects its self-contained `hosted/epg-diagnostics.js` panel,
loaded only when diagnostics are opened; it belongs to the same immutable graph.
Preparation copies exact release bytes and hashes all seven assets. A changed
server worker or diagnostics panel therefore changes both the graph address and profile hash.
The release must advertise `window.__OTT_HOSTED_EPG_SERVER_VERSION__ = 1`;
older bundles are rejected before they can ignore server mode and silently
resume the full-feed download.

`source` and `workerUrl` remain in the profile for explicitly configured custom
XMLTV feeds. A profile containing custom or mixed sources uses the local XMLTV
worker as a whole, preserving source precedence; no private URL, playlist URL,
stream URL, credential or request-selected upstream is sent to this server.
A server error must not silently start downloading the public full XMLTV feed
on the TV. Keep usable local programme data and show a bounded retry/error state.

## Fixed routes and data contract

- `POST /epg/v1/match` proxies only to
  `https://epg.2560801.xyz/epg/v1/match`, with `1200/hour/ip`.
- `GET /epg/v1/programmes` proxies only to
  `https://epg.2560801.xyz/epg/v1/programmes`, with `7200/hour/ip`.

These limits allow matching batches and guide navigation while bounding abuse;
clients still coalesce duplicate requests and cache accepted results. There is
no `/epg/*` wildcard and no arbitrary URL parameter. The JSON POST contains only
`version: 1`, fixed `source: "epg-one"` and up to 2048 channel metadata entries
(`id`, `tvgId`, `tvgName`, `name`). It contains no stream or playlist addresses.

A match response binds mappings to an opaque `generation`, `fetchedAt` in Unix
milliseconds, `refreshMs` and `stale`. Each mapping has `channelId`, `shift` in
integer seconds and `logo`. Programme GET requires `channelId`, `shift`,
`hours` and the accepted `generation`. Rows retain `time`, `time_to` in Unix
seconds and `name`, `descr`, `icon`; the server has already applied the shift.
`hours=0` requests 48 hours of history and 48 hours of future coverage. Positive
hours select the archive window, bounded by the API to 8784.

A generation mismatch returns `409 EPG_GENERATION`: rematch before requesting
new rows. A cold service returns `503 EPG_NOT_READY` with `Retry-After: 5`;
unknown channels return 404. Responses use `Cache-Control: no-store`; inspect
actual proxy headers during acceptance rather than assuming that here.now
preserves every upstream header. here.now publicly documents routing, query
forwarding and response streaming, not arbitrary compute or an EPG cache:
[proxy documentation](https://here.now/docs#proxy-routes).

Matching has an eight-second deadline including queue time. At most two requests
are admitted, with one executing against the shared index. A disconnected request
cancels its work; expiry returns `504 EPG_TIMEOUT` without partial mappings.
The player preserves its saved guide and reports the timeout in diagnostics.

`/epg/v1/health` is for Kubernetes readiness and stays outside the here.now
public proxy manifest. In `EPG_ONLY=true` the dedicated process exposes only the
new EPG API and health; legacy playlist, matching, SVG, proxy and debug routes
must remain inaccessible. Include `.herenow/proxy.json` on every publication:
omitting it removes proxy routes. Inspect finalize warnings explicitly.

## Remote CLI programme search

The control CLI uses `POST https://epg.2560801.xyz/epg/v1/current` directly for
configured `ott PLAYER p` queries. This separate route matches the player's
channel metadata and selects current programmes from one server snapshot. The
player does not load channel schedules for the command, and the CLI does not
send its command-server access token to the EPG service. Title search is
case-insensitive; channels without a current programme are omitted.

This CLI route is allowed by the narrow Cloudflare ingress expression. It does
not require another here.now proxy route or change the browser guide contract
above. Deploy compatible control-server and player releases before configuring
the CLI's public `epg-one` service. A failed query reports an error instead of
falling back to a player-wide guide scan. See the player's
[remote EPG protocol](https://github.com/open-ott-play/ottplay-foss/blob/main/docs/remote-epg-control.md)
for receipt validation, metadata limits and provider matching.

## Deployment sequence

1. Build and qualify a current official Rust server artifact with the bounded
   EPG v1 API. Import the verified OCI bytes and pin their immutable image digest
   in `deploy/epg/ottplay-epg.yaml`. Record release/source/image identity; never
   restore the historical v1.1.43 pin.
2. For initial provisioning, apply the dedicated Deployment, Service and
   connector policy, and the exact DNS/ingress entries from the infrastructure
   repository. For an existing installation, update only the image with a fresh
   deployment identity/resource-version guard; preserve its configuration and
   service/network resources. Qualify
   the actual official ARM64 image on node `h7` during its initial
   download: accepted readiness and REN rows, exact image/container identity,
   responsive health, no restart/OOM/limit-pressure event, and authoritative
   cgroup peak below the 2 GiB limit; retain current memory and process RSS too.
   Observe `/health` while readiness is cold503 where possible. A collector
   started after readiness cannot establish cold-load responsiveness. Do not
   publish the server-mode player while the backend is cold or unavailable.
3. Keep the optimized, source-matched local benchmark of retained old and new
   snapshots separate from official-image measurements. It measures refresh
   overlap locally, not a Linux/musl refresh on `h7`, and excludes the retained
   production compressed buffer. Retain large-playlist match latency and
   failed-refresh evidence that a usable generation and its age are preserved.
   The 2048-channel match check must finish within the player's 12-second request
   timeout and return the expected mappings, including omitted unmatched channels.
4. Prepare a compatible official frontend artifact, review the exact three-route
   proxy manifest and seven-asset graph, then validate a preview against the backend.
5. Publish through the protected workflow only after preview acceptance; verify
   the production runtime and an ordinary reload with a previously primed cache.

Optionally continue observing the same official container through its normal
refresh, approximately two hours after the accepted `fetchedAt`. A second
accepted generation, unchanged container identity, continuous readiness/health
and updated cgroup peak establish that refresh measurement. This follow-up is
separate from initial deployment qualification; record it as pending until
observed.

The server writes bounded `phase=download`, `phase=decode`, `phase=parse` and
`phase=index` lines to container logs, with elapsed milliseconds and public-feed
byte/record counts. These separate source download and processing costs from
client request latency. Read them with
`kubectl --context k3s-heaven -n synology-apps logs deploy/ottplay-epg -c epg`.
Refresh failures retain the accepted snapshot and emit a safe error line without
raw provider data. Qualify placement as well as code: the same deployment limits
do not imply comparable single-thread performance across cluster nodes.

The removed deployment manifest at
`d4bd1f53af81c781c57e6567239d3a28fd6869c4:deploy/epg/ottplay-epg.yaml` is historical
ownership evidence only. Its old image and readiness protocol are incompatible
with this deployment contract. The previous [retirement runbook](epg-retirement.md)
must not be executed while the server-mode profile is live.

## Acceptance

Run `python3 scripts/check-epg.py https://player.ottplay.here.now` after readiness.
The protected publication workflow checks this same approved public client route
before and after cutover using `scripts/check-public-epg.py ./dist`. It validates
the exact staged proxy configuration before making requests and rejects stale,
two-hour-old or far-future generations. A separate direct-origin readiness
check belongs to the operator's approved network; hosted-runner geography must
not require changing Cloudflare access rules. See the
[publication contract](release-workflow.md#server-epg-cutover-using-the-next-qualified-beta).
This names-only smoke check verifies generation binding, a current РЕН ТВ HD
programme with description, and archive rows through the v1 proxy. It does not
replace the following browser/TV acceptance:

- For the default public source, capture requests with fresh browser storage:
  match and programmes use only the two same-origin routes. **No browser request
  to the public XMLTV feed, its redirects or the direct EPG server origin**, and
  no full-feed download/parse worker starts. Measure first usable guide time on
  LG separately from desktop; do not substitute desktop results for TV timing.
- Verify РЕН ТВ HD identity, current title/description, ordered archive rows and
  actual archive playback. Compare row fields and shifts against the accepted
  source generation. Reopening EPG while data is loading must allow navigation
  and Back; video playback must stay responsive.
- Open diagnostics from the actual published asset, then verify Back and navigation
  during loading, a failed load and reopening. Loading diagnostics must not block
  guide requests or reopen a panel after its owner has left the screen.
- Repeat with warm local data, channel changes and a changed playlist. Trigger
  a generation change, cold503, timeout and failed refresh: bound retries,
  rematch on409 and preserve useful local data. Never fall back automatically
  to the public full-feed download when the server fails.
- Explicit custom/mixed feeds must retain their local processing and ordering;
  their URLs and credentials must never appear in server match/query requests.
- Preserve all14 demo media files, both MSX files, encrypted SWOP pairing,
  VPortal and optional control discovery. Verify old `/m3u/match-channels`,
  `/m3u/match-logos`, `/epg/<hash>.json`, `/logo/*`, `/m3u/cp.php`, debug and
  wildcard routes remain unavailable.
- Inspect live owner file inventory, finalize warnings and proxy response
  headers. Check an ordinary cached-tab reload loads the new entry/profile and
  complete immutable worker graph without intercepting network requests.
