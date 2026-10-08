# Dedicated EPG machine API

This module adds a narrow public JSON API on a dedicated `workers.dev` hostname.
It uses one Workers VPC Service to reach the existing private `ottplay-epg`
service through an existing Cloudflare Tunnel. It does not call the public EPG
hostname, change zone Bot Fight Mode, or grant access to a private network.

The browser keeps its same-origin here.now API. Only the two reviewed here.now
upstreams change after acceptance. The CLI uses the same new origin for
`POST /epg/v1/current` through its existing `epg.url` configuration.

```text
browser -> here.now fixed proxy -> dedicated EPG Worker --+
CLI ---------------------------> dedicated EPG Worker --+-> fixed VPC Service
                                                          -> existing Tunnel
                                                          -> ottplay-epg:8080
```

## Scope and limits

Only these three routes are public:

- `POST /epg/v1/match`: uncompressed JSON, at most 512 KiB; no query.
- `POST /epg/v1/current`: the same request limit; no query.
- `GET /epg/v1/programmes`: no request body; exactly one each of `channelId`,
  `shift`, `hours`, and `generation`, with the existing server's field bounds.

Every request requires HTTPS and the exact configured public hostname. Other
routes, including health, media, legacy APIs, and proxy destinations, are rejected.
The fixed VPC binding selects the target independently of the URL passed to
`fetch`. There is no public-origin fallback and no redirect following.
The API returns public EPG data and does not authenticate users. Origin,
User-Agent, and arbitrary authorization headers do not grant privileges.
The private Rust server retains JSON schema validation, source restrictions,
generation matching, admission limits, and its eight-second match deadline.

The relay forwards only its fixed JSON/identity transport headers. Cookies,
authorization, client IP headers, and caller routing hints never reach the
private service. It preserves JSON response bytes and legitimate server status
codes, including 409 generation changes, 429 saturation, and 503 cold readiness.
Responses carry `no-store`, `nosniff`, and `no-referrer`; only a bounded numeric
`Retry-After` from 429/503 responses is retained.

Wire response limits are 4 MiB for match, 2 MiB for current, and 32 MiB for
programmes. The last is deliberately larger than the server's **8 MiB decoded
UTF-16 row budget**: JSON escaping can expand a code unit to six ASCII bytes,
and serialization adds metadata. The current endpoint already bounds serialized
JSON at 2 MiB. The match cap is an additional transport bound, matching the
existing operator smoke check; pathological oversized match metadata is rejected.
Responses stream without JSON parsing, reserialization, or whole-response copies.
The full exchange has a twelve-second deadline, including request/response body
reads. An invalid response detected before headers returns a generic JSON error.
Overflow, truncation, timeout, or cancellation after response streaming begins
terminates the stream; a partial body is never a complete valid response. Clients
must handle that transport failure using their normal error path.

Three mandatory rate-limit bindings provide separate burst protection. Defaults
are 120 match, 600 programme, and 60 current requests per minute per connecting
address at each Cloudflare location. Namespace IDs must be unused and distinct.
Missing bindings or a missing platform client address fail closed before VPC
access. `CF-Connecting-IP` is trusted only because Cloudflare supplies it on the
dedicated Worker ingress; there is no direct origin listener or forwarded-IP
fallback. The public Worker must not be placed behind a new unreviewed intermediary.

These counters are approximate and local to a Cloudflare location, not global
quotas. here.now may appear as a shared Cloudflare egress address: these are
aggregate burst limits for that traffic, not individual viewer limits. Retain
the existing here.now limits of 1200/hour/IP and 7200/hour/IP, and qualify expected
concurrency before cutover. Server admission control remains authoritative.

## Cost and resource ownership

Workers VPC is free during open beta. Workers Free currently includes 100,000
requests per day and 10 ms CPU per invocation; other account Workers consume the
same account allowance. Verify current usage, availability, and CPU measurements
before publication. Network waiting does not consume CPU, but copying or parsing
large bodies does. Exceeding a Free limit can fail requests. This module does not
activate a trial, upgrade a plan, or configure billing.

The standalone Terraform root pins provider 5.24.0 and owns exactly four new
resources: one VPC Service, one Worker, one immutable version, and one deployment.
It adds no zone, DNS, WAF, Access, network policy, or tunnel-ingress resources.
It does not adopt or modify the existing Alexa/Google relay. Observability,
logpush, tail consumers, and preview URLs are disabled. `publish` defaults to
`false`. Keep one state owner; do not manage this Worker separately with Wrangler.

## Offline verification

From the repository root, with Node.js and Terraform installed:

```sh
node --test deploy/epg-worker-vpc/relay.test.mjs
terraform -chdir=deploy/epg-worker-vpc/terraform init -backend=false -lockfile=readonly
terraform -chdir=deploy/epg-worker-vpc/terraform fmt -check -recursive
terraform -chdir=deploy/epg-worker-vpc/terraform validate
terraform -chdir=deploy/epg-worker-vpc/terraform test
```

The Terraform tests use a mock provider and do not create resources. Offline
tests do not prove live VPC connectivity, CPU usage, quota headroom, or playback.

## Deployment and cutover

1. Confirm the existing account, unused Worker name, existing workers.dev
   namespace, unused rate-limit namespace IDs, quota, and compatible QUIC tunnel
   connectors. Verify that **every** selected tunnel connector can reach the
   dedicated EPG Service address and port from its own network namespace. An
   existing narrow egress policy can be reused only when its selectors and port
   actually match. Do not assume an arbitrary pod's reachability proves VPC.
2. Use a dedicated protected Terraform state. Copy `terraform.tfvars.example`
   outside Git and replace every illustrative identity with verified values.
   Supply the existing operator credential through the established environment;
   no credential belongs in variables, source, command arguments, or reports.
   A plan with `publish=false` must contain only the four dedicated resources.
   Preserve the plan privately and review the exact target, bindings and source
   hash before applying it. This repository does not auto-apply the module.
3. After reviewing publication, enable only the dedicated production URL.
   Validate all three legitimate routes over the real VPC path, cold/stale and
   generation-conflict behavior, response sizes, burst handling and CPU usage.
   Verify that malformed, oversized, unknown-route and redirect attempts never
   create arbitrary private-network access. Do not treat an edge-only rejection
   as proof of a functioning backend connection.
4. In a separate reviewed change, switch only here.now's exact match/programmes
   upstreams and their pinned verification contracts. Preserve methods, headers,
   rate limits and the full owner-verified publication inventory. Keep the
   explicit new-origin pre-publication checks for all three routes and the
   public here.now post-publication checks, plus real browser/cache acceptance.
   A repair must not depend on the old Bot Fight Mode-blocked route passing.
5. After endpoint acceptance, update only `epg.url` in the CLI configuration,
   preserving `epg.source` and every other key. Test current programme search
   through the installed CLI. A here.now-only change does not repair CLI access.
6. Retain the previous site version, CLI URL and dedicated Worker version/state
   for recovery. Restoring a URL subject to Bot Fight Mode is a configuration
   rollback, not proof of service availability. Remove resources only after no
   active configuration depends on them. Retire the old public EPG tunnel route
   only in its owner's separate reviewed change.

References: [VPC Services](https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/),
[VPC pricing](https://developers.cloudflare.com/workers-vpc/platform/pricing/),
[Workers limits](https://developers.cloudflare.com/workers/platform/limits/),
[rate-limit locality and accuracy](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/),
[here.now proxy routes](https://here.now/docs#proxy-routes).
