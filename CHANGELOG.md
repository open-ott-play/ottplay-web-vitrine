# Changelog

## Unreleased

### Security

- Enforce TLS 1.2+ and OpenSSL security level 2+ for every pinned publisher curl
  call. Weak certificate keys can no longer be enabled by a local `.curlrc`.
  Unsupported backends stop before owner or publisher network requests, and
  HTTP upload/finalization URLs are rejected before any data is transmitted.
- Document the verified Python and GitHub CLI transport profiles and their
  environment limits.

### Upgrade

- Local publishing now requires a curl executable with an active OpenSSL 3
  backend in `PATH`. User `.curlrc` files remain unchanged but are not read by
  publisher commands. Use `OTTPLAY_CURL_SECURITY_LEVEL=3`, `4` or `5` for a
  stricter policy; see the host TLS profile in `docs/security-design.md`.
