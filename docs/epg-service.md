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
Service in `synology-apps` on `k3s-heaven`.
The manifest also owns a narrow, additive egress policy in `cloudflared`, allowing
only the h7 connector pods to reach this service's pods on TCP 8080.
The existing private registry contains
the unmodified linux/amd64 OCI image from the stable `ottplay-foss` v1.1.46 asset:

- Release container archive SHA-256: `a972466973447b602d5aaabb424dda2f04bb18b3ca5d4a51a98e1d5e51d922b2`.
- Image manifest SHA-256: `898c494ac8be47e9e629ebd5704487c2daf78f1067b104e5525f996a4b112ded`.
- Source: `a291141c4c06c6c80727e2e373ba0322a77696fa`, release validation run `36359779914`.

The source manifest, tag, successful Release gate, archive hash and individual
OCI blob hashes were verified before import. Image promotion must retain the
verified bytes; do not substitute a mutable registry tag. The pinned backend
version is independent of the frontend version published to here.now.

The service loads `http://epg.it999.ru/epg2.xml.gz` into memory and refreshes every
two hours. One replica keeps channel matching and subsequent hash lookup on the
same process. Restarting clears the in-memory channel registry; clients must
reload their playlist afterward. `/health` confirms HTTP availability; readiness
additionally waits for a real РЕН ТВ HD match so a cold server cannot serve empty
matches. Parsing the full XMLTV feed on this node takes several minutes. Memory
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
