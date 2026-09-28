# M3U programme guide on here.now

The hosted profile removes the EPG server dependency. `local/hosted.js` selects
`https://cdn.epg.one/epg2.xml.gz`, `/hosted/epg-worker.js` and a two-hour refresh.
The player downloads and parses XMLTV in a Web Worker, matches the current M3U
channels using the shared matching logic and retains the required programmes.
A validated cache in IndexedDB supports subsequent opens and refresh failures.
No scheduled CI job, k3s service or operator-managed Cloudflare Worker is needed
to refresh this profile. External XMLTV and IPTV content providers remain inputs.

The full feed is intentionally used: the smaller public feed was observed with
no current РЕН ТВ programme, and tested per-channel endpoints were unavailable.
Desktop proof of the full-feed algorithm does not establish LG performance.
Use the actual supported TV/browser builds for cold-start and cache validation.

## Acceptance

Validate РЕН ТВ HD against the current time after a cold load and a warm reload.
Check channel matching, archive selection, a changed playlist, and continued UI
responsiveness while a feed refresh and video playback overlap. Test a failed,
truncated or empty feed without destroying a usable previous cache. Confirm
request logs contain no calls to `epg.2560801.xyz`, `/m3u/match-channels`,
`/m3u/match-logos`, legacy `/epg/<hash>.json`, or the retired SVG endpoint.

Browser storage availability and actual LG memory/startup time are release
criteria. The compressed full feed is tens of MB; avoid constructing a full XML
DOM or expanded XML string. Small same-origin Range proxy chunks remain a
separate optimization until their headers, integrity and TV behavior are tested.
The old `scripts/check-epg.py` tests the former server API only and is not an
acceptance check for the client profile.

## Retire the dedicated backend after cutover

1. Publish and verify the compatible frontend, first-load profile, Worker assets,
   demo media, MSX, encrypted Site Data pairing and VPortal.
2. Confirm no live player request reaches the previous EPG hostname.
3. Delete only the resources owned by `deploy/epg/ottplay-epg.yaml` in
   `k3s-heaven`: the EPG Deployment/Service and its dedicated NetworkPolicy.
4. Remove the dedicated `epg.2560801.xyz` DNS and h7 ingress entries through
   `4alvit/terraform-cloudflare-alvit`; preserve all other h7 services.
5. Verify the public player again and record the deployment/version used.

The following manifest and image information is historical rollback evidence,
not an instruction to deploy a backend for the new hosted profile.

## Historical backend and ownership

`deploy/epg/ottplay-epg.yaml` owns a single Kubernetes Deployment and ClusterIP
Service in `synology-apps` on the `mp` node of `k3s-heaven`.
The manifest also owns a narrow, additive egress policy in `cloudflared`, allowing
only the h7 connector pods to reach this service's pods on TCP 8080.
The existing private registry contains
the unmodified linux/amd64 OCI image from the stable `ottplay-foss` v1.1.43 asset:

- Release container archive SHA-256: `77146883063a9ff656c973ebaf862bbdce2a0a19bee35e2c5c7328116156848b`.
- Image manifest SHA-256: `345f53b4cbb9c255dbdea65d65af8a74ff90095535478d8ba701918271976ae0`.
- Source: `9eb64715c4630ef59235e4a97ac3b5a769ff041f`, release validation run `35743019208`.

The source manifest, tag, successful Release gate, archive hash and individual
OCI blob hashes were verified before import. Image promotion must retain the
verified bytes; do not substitute a mutable registry tag. The pinned backend
version is independent of the frontend version published to here.now.

The v1.1.46 server was tested first, but its shared-runtime XMLTV processing
was too slow for this workload: a single РЕН ТВ HD lookup took 33.76 seconds
on the deployed node and repeatedly exceeded readiness timeouts. The v1.1.43
server retains the compatible text/JSON protocol and native Rust parser.
Before upgrading this backend pin, measure a full-feed cold start and a large
playlist lookup; small parser unit tests alone do not establish usable latency.

The service loads `https://cdn.epg.one/epg2.xml.gz` into memory and refreshes every
two hours. This is the CDN destination of the default `epg.it999.ru` feed; using
it directly over HTTPS avoids an unavailable intermediate HTTP redirect.
One replica keeps channel matching and subsequent hash lookup on the
same process. Restarting clears the in-memory channel registry; clients must
reload their playlist afterward. `/health` confirms HTTP availability; readiness
additionally waits for a real РЕН ТВ HD match so a cold server cannot serve empty
matches. Memory
headroom allows the old cache and a replacement feed to coexist during refresh.
Run the programme smoke check before enabling routes.

`4alvit/terraform-cloudflare-alvit` owns the proxied `epg.2560801.xyz` DNS record
and the h7 tunnel route to `http://ottplay-epg.synology-apps.svc.cluster.local:8080`.
Its anchored path allowlist exposes only matching, programme JSON and generated
logos. Other paths return 404, including `/m3u/cp.php`, VPortal and debug APIs.
No playlist credentials or stream URLs are needed to populate this cache.
