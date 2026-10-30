# Install an isolated hosted Stalker relay

This installation serves only explicitly configured Stalker routes using an
official `ottplay-server` image. It does not reactivate the stopped Synology
stack, expand the EPG service or modify the Go command/normalization services.
The player/release owner coordinates activation; this repository never deploys
these resources automatically when an image or site is released.

## Ownership and prerequisites

`ottplay-foss` owns the Rust API and official OCI release artifact.
`ottplay-web-vitrine` owns the new Kubernetes application resources and the
separate `deploy/stalker-worker-vpc/terraform` root. Existing h7 tunnel/DNS stays
in `terraform-cloudflare-alvit`; cluster and connectors stay in
`k3s-self-healing`. Use a new protected Terraform state, never EPG's state.

The existing accepted EPG installation documents an h7 tunnel and the
`igw-2560801.workers.dev` account namespace. A new Worker named
`ottplay-stalker-api` would use
`https://ottplay-stalker-api.igw-2560801.workers.dev`. This document does not claim
that name is deployed or available. Verify the account/namespace, unused name,
unused rate-limit namespace, quota and compatible connectors before creating it.
The first plan has `publish=false`; a postcondition verifies the expected
workers.dev hostname. Four new resources suffice: fixed VPC Service, Worker,
immutable Worker version and deployment. No new DNS or tunnel-ingress entry is
required. Do not point the VPC Service at EPG's ClusterIP.

Before deployment, qualify the selected official ARM64 image with relay-only
flags, its nonroot Secret permissions and realistic catalog concurrency. Set CPU
request and memory request/limit from that qualification. The 64 MiB wire limit
is not a process memory budget. Verify current node h7, namespace `synology-apps`,
connector labels, registry access and available capacity in context `k3s-heaven`.

## Private sources and credentials

Use only the operator's existing authorized fixed portal endpoints. Derive their
canonical API paths with the same client normalizer; do not invent providers or
copy source display names into Git. Private JSON schema:

```text
version: 1
routes: [{name: opaque route ID, upstream: canonical endpoint without query,
          credential: unique random URL-safe relay credential}]
```

The credential is per route and is separate from portal account credentials.
No MAC needs to be stored in this config. Keep the file 0600 outside Git following
`k3s-self-healing/docs/secrets-local.md`. Never commit live JSON, Secret YAML,
credentials, source names or URLs. Supply the matching credential to here.now as
a server variable only. The Worker has no secret value binding and no source list.

Create a versioned Secret from the private file, without command tracing or
printing/diffing its content. This scoped command avoids unrelated secrets:

```sh
set +x
set -o pipefail
kubectl --context k3s-heaven -n synology-apps create secret generic "$SECRET_NAME" \
  --from-file=config.json="$PRIVATE_CONFIG" --dry-run=client -o yaml | \
  kubectl --context k3s-heaven -n synology-apps apply --server-side -f - >/dev/null
```

File path and Secret name are arguments; content is confined to file/pipe/API.
The rendered Pod uses UID/GID 65532, fsGroup 65532, read-only Secret mode 0440 and
read-only root filesystem. No root init container is necessary. Configuration
loads once at startup: rotate using a new Secret reference and Pod rollout;
updating a mounted file alone does not reload routes.

## Verify and render; nothing deploys from this helper

Obtain `release-manifest.json` and `ottplay-foss-container.oci.tar` from the
qualified official release through the normal release owner. Pin the manifest
hash from that independently verified receipt, plus exact source SHA and release
tag. The renderer verifies file/descriptor/blob hashes, the ARM64 config receipt,
source/version labels and nonroot user. It selects the ARM64 leaf manifest digest.
The renderer is an offline integrity check, not a replacement for verifying the
GitHub release provenance or testing that image's relay implementation.

```sh
python3 deploy/stalker-relay/render.py \
  --manifest "$RELEASE_MANIFEST" --manifest-sha256 "$PINNED_MANIFEST_SHA256" \
  --oci "$OFFICIAL_OCI" --source "$SOURCE_SHA" --tag "$RELEASE_TAG" \
  --repository "$REGISTRY_REPOSITORY" --secret "$SECRET_NAME" \
  --cpu-request "$CPU_REQUEST" --memory-request "$MEMORY_REQUEST" \
  --memory-limit "$MEMORY_LIMIT" --output "$NEW_RENDERED_MANIFEST"
```

All arguments are required; resource values have no defaults. The output must not
exist. Verification failure never writes/truncates it. The checked-in
`manifest.json.in` is deliberately invalid JSON until rendered; it cannot be
mistaken for an apply-ready manifest. The output contains no source list or
credentials. It has exactly Deployment, ClusterIP Service and two NetworkPolicies.
The main command explicitly includes `--host 0.0.0.0 --port 8080` (with separate
arguments): relay mode otherwise defaults to loopback. The image's CMD is
replaced explicitly with `/app/ottplay-server` and the relay-only arguments.

Import the verified ARM64 bytes through the established registry process without
rebuilding. For skopeo's ordinary registry transport, the operation is:

```sh
skopeo --override-os linux --override-arch arm64 copy --preserve-digests \
  "oci-archive:$OFFICIAL_OCI" "docker://$REGISTRY_REPOSITORY:$RELEASE_TAG"
```

Use the installation's existing authenticated registry transport configuration;
do not put registry credentials in command arguments or disable TLS by default.
Confirm the imported leaf digest equals the renderer's reported digest and is
pullable from h7. The manifest uses that digest, never a mutable tag. Qualified
beta OCI can be used for a beta deployment; public registry publication is a
separate verified-stable operation under `ottplay-foss/docs/release-workflow.md`.

## Activate after coordinated release handoff

1. Complete [offline checks](../deploy/stalker-worker-vpc/README.md#offline-checks),
   finish resource qualification, provision the private Secret and import the
   exact image. Review only the new rendered resources.
2. Apply the scoped manifest and confirm running image/source, config readability,
   health and stability:

   ```sh
   kubectl --context k3s-heaven apply --dry-run=server -f "$NEW_RENDERED_MANIFEST"
   kubectl --context k3s-heaven apply --server-side -f "$NEW_RENDERED_MANIFEST"
   kubectl --context k3s-heaven -n synology-apps rollout status deployment/ottplay-stalker-relay --timeout=180s
   kubectl --context k3s-heaven -n synology-apps get service ottplay-stalker-relay -o jsonpath='{.spec.clusterIP}'
   ```

3. Verify every selected h7 connector can reach that actual Service address and
   port 8080. Put it, the verified account/tunnel/namespace identities and qualified
   rate limit into private tfvars outside Git. Keep the new state and saved plans
   outside Git with restrictive permissions. Initialize this new Terraform root
   with the deployment's dedicated protected state path/backend configuration;
   do not migrate or reuse the EPG state. Plan with `publish=false` and apply only
   the saved four-resource plan. Commands below assume that state has been selected:

   ```sh
   terraform -chdir=deploy/stalker-worker-vpc/terraform init -lockfile=readonly
   terraform -chdir=deploy/stalker-worker-vpc/terraform plan -var-file="$PRIVATE_TFVARS" -out="$PRIVATE_PLAN"
   terraform -chdir=deploy/stalker-worker-vpc/terraform apply "$PRIVATE_PLAN"
   ```

4. Use a separate saved plan to enable only the new Worker's production URL.
   Qualify exact HTTPS routes, normal catalog/metadata requests, response sizes,
   cancellation, latency, rate limits and VPC/Worker resource headroom. Unknown
   IDs and invalid credentials must not reach a portal. `/health`, `/stalker/api`,
   generic proxy, media and control routes remain unavailable externally.
5. Only then stage fixed per-source here.now routes with server-held credentials,
   preserve the complete owner-verified site inventory and CAS base, publish and
   perform the normal post-publication checks. This module does not publish sites.

## Updates and rollback

Record source/tag, official archive/manifest hashes, ARM64 digest, Deployment UID
and resourceVersion, Secret reference, Service IP, gateway version and here.now
owner version. Updates change only the image using a fresh UID/resourceVersion
guard, unless a separately intended config rotation is included. Do not overwrite
concurrent configuration, selectors, Service, policies or another app's resources.

Retain previous digest and versioned Secret plus gateway/site versions. Roll back
the here.now route/profile first when needed; roll back this gateway/image/config
without touching EPG, DNS or h7 tunnel ingress. Keep resources while an accepted
site version still depends on them. GitHub release publication alone does not
trigger these operations.
