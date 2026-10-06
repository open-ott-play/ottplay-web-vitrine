# Historical retirement of the dedicated EPG backend

**Archived procedure: do not execute for the current server-mode profile.**
The current hosted deployment again depends on the dedicated Rust EPG service.
Use [the active EPG runbook](epg-service.md) for provisioning and acceptance.
The deletion steps below record the prior client-only cutover and are only
applicable after a separately reviewed replacement removes that dependency.


This change removes the retired deployment manifest from the active source
tree. It does not itself delete live Kubernetes or Cloudflare resources. Merge
and execute this cleanup only after the self-contained here.now profile has
been published and accepted. The older server-backed profile still needs these
resources and must not be deployed afterward.

## Acceptance before any deletion

- The published player loads current РЕН ТВ HD EPG from the public XMLTV source
  in its own browser worker and reuses the IndexedDB cache after reopening.
- M3U playback and guide browsing work with access to the old EPG origin
  blocked. Browser traffic contains no `/m3u/match-channels`,
  `/m3u/match-logos`, server-backed `/epg/` or `/logo/` requests.
- The new here.now proxy/profile manifest contains no EPG or SWOP routes to
  our `2560801.xyz` origins. Demo and MSX files remain present.
- Keep a known-good publication version and the historical backend manifest
  available for rollback.

## Exact Kubernetes deletion scope

Use context `k3s-heaven`. The original manifest owned only these resources:

- `Deployment/synology-apps/ottplay-epg` — one EPG pod on node `mp`; deleting
  its controller also removes its owned ReplicaSets and Pods.
- `Service/synology-apps/ottplay-epg` — internal ClusterIP, TCP8080.
- `NetworkPolicy/cloudflared/ottplay-epg-egress` — the additive rule permitting
  h7 connector pods to reach only the dedicated EPG pod on TCP8080.

Preview the named resources before executing the explicit deletions:

```sh
kubectl --context k3s-heaven -n synology-apps get \
  deployment/ottplay-epg service/ottplay-epg
kubectl --context k3s-heaven -n cloudflared get \
  networkpolicy/ottplay-epg-egress

kubectl --context k3s-heaven -n synology-apps delete \
  deployment/ottplay-epg service/ottplay-epg --wait=true
kubectl --context k3s-heaven -n cloudflared delete \
  networkpolicy/ottplay-epg-egress --wait=true
```

Do not delete either namespace, node `mp`, the private registry, the shared
cloudflared-h7 Deployment, the h7 tunnel, or any broader NetworkPolicy. This
installation created no PVC or database. The retained release image is an
inactive artifact; deleting shared registry data is not part of this cleanup.

The dedicated DNS record and two h7 ingress entries have a separate
infrastructure owner. Use the operator's bounded retirement plan for those
exact resources and preserve all other routes. Preserve the separately owned SWOP Worker: this retirement does not
establish that all its other clients have migrated.

After deletion, verify the named resources are absent, all unrelated h7 routes
remain healthy, and current/cached EPG still works at the published player.

## Rollback material

The original manifest is preserved verbatim in repository history at
`d4bd1f53af81c781c57e6567239d3a28fd6869c4`:

```sh
git show d4bd1f53af81c781c57e6567239d3a28fd6869c4:deploy/epg/ottplay-epg.yaml \
  > /tmp/ottplay-epg-rollback.yaml
```

It pins the verified v1.1.43 image by digest. Restore that exact manifest and
the dedicated Terraform origin, wait for real EPG readiness, and only then
restore a server-backed here.now publication. A website rollback by itself
cannot recreate a removed backend.
