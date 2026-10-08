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
- [tests/test_retain_runtime.py](../tests/test_retain_runtime.py)
- [tests/test_swop.py](../tests/test_swop.py)

Run the documented commands in [CONTRIBUTING.md](../CONTRIBUTING.md) and the
[CI workflow](../.github/workflows/validate.yml). Preserve negative tests for rejected inputs,
unavailable dependencies, authorization failures and cancellation. A passing
test run describes its fixtures and environment; it does not certify every
upstream service, hardware model or production deployment.

## Remaining security assessment

Assess version/release-note applicability for validation-only publication tooling separately from upstream player versions, and verify delivery/signature and dynamic-analysis evidence.

Report new issues through [SECURITY.md](../SECURITY.md). An OpenSSF assessment
records evidence and applicability; it is not a guarantee that a system is safe.
