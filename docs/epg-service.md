# Server-prepared M3U programme guide on here.now

The reviewed hosted profile selects `mode: "server"`, `sourceId: "epg-one"` and
same-origin `apiBase: "/epg/v1"`. A dedicated Rust EPG service downloads and
indexes the public `https://cdn.epg.one/epg2.xml.gz` source once for all clients,
refreshing every two hours. The LG no longer downloads or parses that full feed
for the default public source. It receives channel mappings and only the guide
window it needs. here.now serves the player and forwards two fixed API routes;
it does not execute our Rust process or schedule XMLTV processing.

The service is external infrastructure: `Deployment/ottplay-epg` and
`Service/ottplay-epg` in `synology-apps` on `k3s-heaven`. Cloudflare's h7 tunnel
connects `epg.2560801.xyz` to the service. DNS and tunnel ingress are owned by
`4alvit/terraform-cloudflare-alvit`; publication of this repository alone does
not provision them. The narrow `NetworkPolicy/ottplay-epg-egress` in
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

`/epg/v1/health` is for Kubernetes readiness and stays outside the here.now
public proxy manifest. In `EPG_ONLY=true` the dedicated process exposes only the
new EPG API and health; legacy playlist, matching, SVG, proxy and debug routes
must remain inaccessible. Include `.herenow/proxy.json` on every publication:
omitting it removes proxy routes. Inspect finalize warnings explicitly.

## Deployment sequence

1. Build and qualify a current official Rust server artifact with the bounded
   EPG v1 API. Import the verified OCI bytes and deploy by immutable image digest.
   Record release/source/image identity; never restore the historical v1.1.43 pin.
2. Apply the reviewed dedicated Deployment, Service and connector policy, and
   the exact DNS/ingress entries from the infrastructure repository. Wait for a
   nonempty accepted generation and readiness. Do not publish a server-mode
   player while this dependency is cold or unavailable.
3. Check the backend's full-feed refresh cost and large-playlist match latency.
   Confirm a failed refresh preserves a usable generation and reports its age.
4. Prepare a compatible official frontend artifact, review the exact three-route
   proxy manifest and seven-asset graph, then validate a preview against the backend.
5. Publish through the protected workflow only after preview acceptance; verify
   the production runtime and an ordinary reload with a previously primed cache.

The removed deployment manifest at
`d4bd1f53af81c781c57e6567239d3a28fd6869c4:deploy/epg/ottplay-epg.yaml` is historical
ownership evidence only. Its old image and readiness protocol are incompatible
with this deployment contract. The previous [retirement runbook](epg-retirement.md)
must not be executed while the server-mode profile is live.

## Acceptance

Run `python3 scripts/check-epg.py https://player.ottplay.here.now` after readiness.
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
