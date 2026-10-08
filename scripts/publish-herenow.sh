#!/usr/bin/env bash
# Publish a static dist/ tree to here.now workspace ottplay (player.ottplay.here.now).
# Site slug for player.ottplay.here.now label: liminal-sketch-vv8r
set -euo pipefail

DIST="${1:-./dist}"
WORKSPACE="${HERENOW_WORKSPACE:-ottplay}"
CLIENT="${HERENOW_CLIENT:-github-actions/ottplay-web-vitrine}"
SLUG="${HERENOW_SITE_SLUG:-}"
EXPECTED_VERSION="${HERENOW_EXPECTED_VERSION:-}"
SPA="${SPA:-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -d "$DIST" ]]; then
  echo "error: dist directory not found: $DIST" >&2
  exit 1
fi
if [[ ! -f "$DIST/index.html" ]]; then
  echo "error: $DIST/index.html missing — publish the site root, not a parent folder" >&2
  exit 1
fi

# Refuse a destructive full-site replacement with an incomplete demo, including
# when this wrapper is called directly instead of through prepare-dist.py.
python3 "$(dirname "${BASH_SOURCE[0]}")/demo_media.py" "$DIST"
python3 "$(dirname "${BASH_SOURCE[0]}")/msx.py" "$DIST"
python3 "$(dirname "${BASH_SOURCE[0]}")/swop.py" "$DIST" --runtime

if [[ "${OVERWRITE:-0}" != "0" && "${OVERWRITE:-0}" != "false" ]]; then
  echo 'error: unchecked overwrite is disabled; reconcile the live owner inventory and provide HERENOW_EXPECTED_VERSION' >&2
  exit 1
fi
if [[ -n "$SLUG" ]]; then
  if [[ ! "$EXPECTED_VERSION" =~ ^[a-zA-Z0-9_-]{1,128}$ ]]; then
    echo 'error: updates require HERENOW_EXPECTED_VERSION from the reviewed owner inventory' >&2
    exit 1
  fi
elif [[ -n "$EXPECTED_VERSION" ]]; then
  echo 'error: HERENOW_EXPECTED_VERSION requires HERENOW_SITE_SLUG' >&2
  exit 1
fi

if [[ -z "${HERENOW_API_KEY:-}" && ! -f "${HOME}/.herenow/credentials" ]]; then
  echo "error: set HERENOW_API_KEY or write ~/.herenow/credentials" >&2
  exit 1
fi

PUBLISH_SH="${HERENOW_PUBLISH_SCRIPT:-$PWD/.ci-tools/herenow/here-now/scripts/publish.sh}"
if [[ ! -x "$PUBLISH_SH" ]]; then
  echo 'Check out heredotnow/skill at 8cf033ed53b82c0c67b16359c8c431f99e111d04 into .ci-tools/herenow first.' >&2
  exit 1
fi
PUBLISH_SH="$(cd "$(dirname "$PUBLISH_SH")" && pwd)/$(basename "$PUBLISH_SH")"
PUBLISH_ROOT="$(cd "$(dirname "$PUBLISH_SH")/../.." && pwd)"
if [[ "$(git -C "$PUBLISH_ROOT" rev-parse HEAD)" != '8cf033ed53b82c0c67b16359c8c431f99e111d04' ]]; then
  echo 'Publisher revision differs from the reviewed CI pin.' >&2
  exit 1
fi
if [[ -n "$(git -C "$PUBLISH_ROOT" status --porcelain --untracked-files=no)" ]]; then
  echo 'Publisher checkout contains local modifications.' >&2
  exit 1
fi

# Use one verified TLS backend and policy for every curl subprocess in the
# pinned Bash publisher. Disable per-user curl config for these calls only.
# No caller-controlled extra arguments are accepted by this wrapper.
OTTPLAY_PUBLISH_CURL="$(type -P curl)" || {
  echo 'error: publishing requires curl with an active OpenSSL 3 backend' >&2
  exit 1
}
OTTPLAY_PUBLISH_CURL="$(cd "$(dirname "$OTTPLAY_PUBLISH_CURL")" && pwd)/$(basename "$OTTPLAY_PUBLISH_CURL")"
CURL_VERSION="$("$OTTPLAY_PUBLISH_CURL" --disable --version)"
if [[ ! "${CURL_VERSION%%$'\n'*}" =~ (^|[[:space:]])OpenSSL/3\.[0-9]+\.[0-9]+([[:space:]]|$) ]]; then
  echo 'error: publishing requires curl with an active OpenSSL 3 backend; select it in PATH' >&2
  exit 1
fi
OTTPLAY_CURL_SECURITY_LEVEL="${OTTPLAY_CURL_SECURITY_LEVEL:-2}"
if [[ ! "$OTTPLAY_CURL_SECURITY_LEVEL" =~ ^[2-5]$ ]]; then
  echo 'error: OTTPLAY_CURL_SECURITY_LEVEL must be 2, 3, 4 or 5' >&2
  exit 1
fi
export OTTPLAY_PUBLISH_CURL OTTPLAY_CURL_SECURITY_LEVEL
curl() {
  "$OTTPLAY_PUBLISH_CURL" --disable --tlsv1.2 --proto '=https' \
    --ciphers "DEFAULT:@SECLEVEL=$OTTPLAY_CURL_SECURITY_LEVEL" "$@"
}
export -f curl

DIST="$(cd "$DIST" && pwd)"
ARGS=( "$DIST" --workspace "$WORKSPACE" --client "$CLIENT" )
if [[ -n "$SLUG" ]]; then
  ARGS+=( --slug "$SLUG" )
fi
if [[ "$SPA" == "1" || "$SPA" == "true" ]]; then
  ARGS+=( --spa )
fi

# The pinned publisher has no HLS extension mapping and falls back to file(1),
# which can report text/plain or application/octet-stream. Supply the existing
# demo MIME types to that fallback without modifying the reviewed dependency.
file() {
  if [[ "$#" == 3 && "$1" == "--brief" && "$2" == "--mime-type" ]]; then
    case "$3" in
      */demo/pattern.m3u8) printf '%s\n' 'application/vnd.apple.mpegurl'; return ;;
      */demo/pattern[0-9]*.ts) printf '%s\n' 'video/mp2t'; return ;;
    esac
  fi
  command file "$@"
}
export -f file

echo "Publishing $DIST → workspace=$WORKSPACE slug=${SLUG:-'(create/new)'} via $PUBLISH_SH …" >&2
if [[ -n "$SLUG" ]]; then
  # The reviewed publisher reads baseVersionId from state scoped to the absolute
  # source path. Seed only the explicitly accepted version in a private cwd;
  # never adopt a newer live version or a previous local checkout's state.
  # here.now checks this base on both the update PUT and its later finalize.
  (
    umask 077
    PUBLISH_STATE="$(mktemp -d "${TMPDIR:-/tmp}/ottplay-publish.XXXXXXXX")"
    trap 'rm -rf "$PUBLISH_STATE"' EXIT
    # Keep the strict accepted stage intact. Only authenticated, version-bound
    # immutable graphs may be added to this private publication copy.
    python3 "$SCRIPT_DIR/retain_runtime.py" "$DIST" "$PUBLISH_STATE/site" \
      --receipt "$PUBLISH_STATE/retention.json" --workspace "$WORKSPACE" \
      --slug "$SLUG" --expected-version "$EXPECTED_VERSION"
    DIST="$PUBLISH_STATE/site"
    ARGS[0]="$DIST"
    mkdir "$PUBLISH_STATE/.herenow"
    python3 - "$SLUG" "$EXPECTED_VERSION" "$DIST" "$PUBLISH_STATE/.herenow/state.json" <<'PY'
import json
import sys

slug, version, source, target = sys.argv[1:]
with open(target, "x", encoding="utf-8") as output:
    json.dump({"publishes": {slug: {"versionId": version, "path": source}}}, output)
PY
    cd "$PUBLISH_STATE"
    "$PUBLISH_SH" "${ARGS[@]}"
  )
else
  "$PUBLISH_SH" "${ARGS[@]}"
fi
