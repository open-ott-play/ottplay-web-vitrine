# Dedicated Stalker HTTPS gateway

This is an independent Worker/VPC deployment. It does not modify the EPG Worker,
its state, DNS, Access policies, tunnel ingress or any control service. The
application repo owns these four resources; the existing tunnel remains owned by
`terraform-cloudflare-alvit`. See [installation](../../docs/stalker-relay.md).

The public contract is HTTPS on exactly `PUBLIC_HOST`, POST on
`/stalker/<opaque-id>`, with no query or fragment. IDs have 1–64 lowercase ASCII
letters/digits separated by single hyphens; `api` is excluded. Only the private
Rust configuration determines which IDs exist. No provider name or URL belongs
in Worker code, Terraform or logs.

A required `X-OTT-Stalker-Relay` header carries 32–256 URL-safe ASCII characters.
Duplicate/merged headers are rejected. The Worker forwards this header and fixed
JSON/identity headers only. It does not copy outer cookies, authorization,
forwarding headers or arbitrary caller metadata. The Rust backend validates the
credential against the selected route; it never sends this operator credential
to the portal. Configure credentials as server-held here.now variables, not
browser-visible settings or static assets.

The sole backend is a Workers VPC Service binding to one verified ClusterIP and
port 8080 through the existing tunnel. `fetch` uses that binding, never the global
network function; the caller cannot select its host or port. Redirects are not
followed. The gateway requires a valid platform-supplied `CF-Connecting-IP` and a
working rate-limit binding. This is an approximate per-location, per-connecting-
address limit; here.now traffic may share one egress address. The rate is an
explicit installation input to qualify, not a guessed default.

Outer request limit: **32 KiB**. Requests are opaque JSON media, without content
encoding; Rust owns protocol validation. Response limit: **64 MiB**, compatible
with the Rust bulk live-catalog action. Ordinary actions remain subject to the
core's smaller bound. Success JSON bytes are streamed without parsing, buffering
the full catalog or reserializing. Native workerd streams use bounded BYOB reads;
the first available response bytes are returned promptly. Declared lengths never
substitute for actual EOF. Overflow, truncation, timeout or cancellation aborts
the backend; an already-started response fails its body rather than reporting
false success.

One **18-second** deadline covers admission, request read, backend headers and
response EOF. It is below the current hosted client's 20-second timeout and is
not restarted at each phase. The core's upstream deadline is 15 seconds. Normal
backend error statuses 400–599 become fixed safe JSON with no upstream details.
Only numeric Retry-After values from 0 to 86400 survive 429/503 errors.
Redirects, non-JSON successful responses and invalid response framing fail 502.
Every response is no-store, nosniff and no-referrer. No request/response logger,
tail consumer, observability, logpush or preview URL is enabled.

## Offline checks

```sh
node --test deploy/stalker-worker-vpc/relay.test.mjs
npm ci --ignore-scripts --prefix deploy/stalker-worker-vpc/runtime-tests
npm test --prefix deploy/stalker-worker-vpc/runtime-tests
terraform -chdir=deploy/stalker-worker-vpc/terraform init -backend=false -lockfile=readonly
terraform -chdir=deploy/stalker-worker-vpc/terraform fmt -check -recursive
terraform -chdir=deploy/stalker-worker-vpc/terraform validate
terraform -chdir=deploy/stalker-worker-vpc/terraform test
python3 -m unittest discover -s tests -p test_stalker_relay_deployment.py -v
```

The Terraform tests use only a mocked provider. Native workerd tests pin the same
runtime as the EPG tests and deny all outside networking. They verify real BYOB
streams, 64 MiB framing, cancellation and the 18-second deadline. Node tests verify
routing, header isolation, length bounds, admission, late replies and streaming.
These tests do not prove live VPC reachability, provider compatibility, quota,
CPU/memory headroom or registry availability.
