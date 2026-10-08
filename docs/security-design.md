# Security design and verification

## Scope and trust boundaries

The project provides verified preparation and publication tooling for the hosted OTT-play frontend.

Release archives and downloaded demo media are untrusted until their provenance, digest and archive paths pass validation. Publishing credentials and SWOP relay secrets remain backend-only. Publication replaces a public site and requires a qualified release and an explicit operator decision. Static hosting cannot replace the separate authenticated media/control backend.

## Source and operating documentation

- [scripts/prepare-dist.py](../scripts/prepare-dist.py)
- [scripts/demo_media.py](../scripts/demo_media.py)
- [scripts/publish-herenow.sh](../scripts/publish-herenow.sh)
- [scripts/swop.py](../scripts/swop.py)

## Regression evidence

- [tests/test_prepare_dist.py](../tests/test_prepare_dist.py)
- [tests/test_publish_cas.py](../tests/test_publish_cas.py)
- [tests/test_publish_tls.py](../tests/test_publish_tls.py)
- [tests/test_retain_runtime.py](../tests/test_retain_runtime.py)
- [tests/test_swop.py](../tests/test_swop.py)

Run the documented commands in [CONTRIBUTING.md](../CONTRIBUTING.md) and the
[CI workflow](../.github/workflows/validate.yml). Preserve negative tests for rejected inputs,
unavailable dependencies, authorization failures and cancellation. A passing
test run describes its fixtures and environment; it does not certify every
upstream service, hardware model or production deployment.

## Host TLS profile

The Python owner-inventory reader (`retain_runtime.py`) and EPG acceptance probe
(`check-epg.py`) use the standard `urllib` HTTPS transport. The supported network
profile is CPython 3.12+ with an OpenSSL default context requiring TLS 1.2 or later,
certificate and hostname verification, and security level 2 or higher. Check the
actual interpreter before passing credentials or publication data:

```sh
python3 - <<'PY'
import ssl
import sys
context = ssl.create_default_context()
print(sys.version)
print(ssl.OPENSSL_VERSION)
print("minimum TLS:", context.minimum_version.name, "security level:", context.security_level)
assert sys.version_info >= (3, 12)
assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
assert context.security_level >= 2
assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
PY
```

On 2026-10-08, CPython 3.12.14 with OpenSSL 3.5.8 passed local probes through both
actual helper transports. Trusted RSA-2048 chains worked over TLS 1.2 and 1.3;
RSA-1024 leaf, intermediate and root keys were rejected before HTTP requests.
This profile does not extend to older interpreters or vendor/system overrides
that weaken their default context.

### Publisher transport

`publish-herenow.sh` resolves the `curl` executable from `PATH` and requires its
active TLS backend to be OpenSSL 3 before any owner-reader or publisher network
operation. All plain `curl` calls in the pinned Bash publisher inherit the
wrapper's function, which invokes that exact executable with `--disable` first,
`--tlsv1.2`, `--proto '=https'`, and `--ciphers DEFAULT:@SECLEVEL=2`.
Creation/update, file uploads and finalization therefore use the same TLS policy;
HTTP upload or finalization URLs returned by the service are rejected before
data is sent. The pinned publisher has no
later TLS options that override it; review this invariant when updating the pin.

`--disable` excludes user curl configuration for these commands only; no
`.curlrc` is edited or deleted. Curl environment settings such as CA locations
and proxies are still operator-controlled. Configure and verify those separately;
this profile is not evidence about a proxy's own TLS connection or credentials.
Normal certificate-chain and hostname verification remain enabled.

The sole policy override is `OTTPLAY_CURL_SECURITY_LEVEL=2`, `3`, `4` or `5`.
Select a higher level explicitly if required; an existing higher setting in
`.curlrc` is not imported. Invalid levels and unsupported TLS backends fail
before network access. For local publication, select an OpenSSL 3 curl in `PATH`
and inspect `curl --disable --version`; the system curl on macOS may use a
different backend and is intentionally rejected.

The regression suite exercises the actual wrapper and unchanged pinned publisher
through a loopback CONNECT tunnel, with synthetic credentials and certificates.
With curl 8.22.0/OpenSSL 3.6.4, TLS 1.2 and 1.3 completed all three stages with
strong chains. Weak leaf, intermediate and root keys were rejected before the
affected stage sent HTTP data, even with `insecure` and security level 0 in the
test `.curlrc`. HTTP upload and finalization URLs were also rejected before
data transmission. A level-3 override rejected RSA-2048. Separate offline tests
verify that unsupported backends stop before even the owner inventory request.
These tests do not publish a site or attest to a production server.

### GitHub CLI transport

`prepare-dist.py`, `release.py` and `require-production.py` delegate GitHub
requests to `gh` and preserve its environment. Use the verified GitHub CLI
2.102.0/Go 1.27.1 profile with `GH_HOST=github.com GODEBUG=fips140=on`; first check
`gh --version` and `go version -m "$(command -v gh)"`. For example, the following
operation is read-only:

```sh
GH_HOST=github.com GODEBUG=fips140=on python3 scripts/release.py status
```

The [shared release TLS profile](https://github.com/victron-venus/venus-os-ci-toolkit/blob/3f7d3ff94d1c693f32f4f92d17f7eb04883b4e7e/docs/RELEASE_TLS_PROFILE.md)
documents the runtime evidence and restrictions. This is a command-local Go
policy, not a FIPS certification claim. Other versions, GitHub Enterprise,
custom endpoints and proxy trust require their own verification. These host
profiles do not establish the cryptography of the browser player, remote
services, DRM modules or devices.

Policy references: [curl options](https://curl.se/docs/manpage.html),
[OpenSSL security levels](https://docs.openssl.org/3.6/man3/SSL_CTX_set_security_level/),
and [Python SSL contexts](https://docs.python.org/3.12/library/ssl.html#ssl.create_default_context).

## Remaining security assessment

Assess version/release-note applicability for validation-only publication tooling separately from upstream player versions, and verify delivery/signature and dynamic-analysis evidence.

Report new issues through [SECURITY.md](../SECURITY.md). An OpenSSF assessment
records evidence and applicability; it is not a guarantee that a system is safe.
