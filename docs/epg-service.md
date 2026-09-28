# M3U programme guide

The browser M3U driver sends channel names to `POST /m3u/match-channels`, then
loads `GET /epg/<hash>.json`. A static host without these routes serves its SPA
HTML instead, leaving every channel without a programme on browsers and TVs.

`static/.herenow/proxy.json` forwards matching, programme JSON, logo matching and
generated SVG logos to `https://epg.2560801.xyz`. Matching permits 600 requests
per hour per IP; programme and logo reads permit 7200. Include this manifest
with every publication. The existing staging and publication validator checks
all routes and refuses missing routes, changed upstreams and broad M3U proxies.

## Backend and ownership

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

## Deployment and verification

```sh
kubectl --context k3s-heaven apply -f deploy/epg/ottplay-epg.yaml
kubectl --context k3s-heaven -n synology-apps rollout status deployment/ottplay-epg
python3 scripts/check-epg.py https://epg.2560801.xyz
python3 scripts/check-epg.py https://player.ottplay.here.now
```

The smoke check submits only the public name РЕН ТВ HD and verifies programme
JSON contains a current programme. Test the origin first, then publish the
manifest with the complete preserved site or a reviewed stable distribution.
Reload the browser/TV playlist so it retries matching after the old HTML response.
Also confirm blocked origin paths return 404 and that demo, MSX, SWOP and VPortal
publication controls remain present. A successful static upload alone does not
prove the proxy manifest was accepted.

For rollback, restore the prior here.now version and revert the dedicated DNS,
tunnel entries and Kubernetes manifest. Removing these routes restores the old
missing-EPG behavior; it must not alter unrelated tunnel routes or player files.
