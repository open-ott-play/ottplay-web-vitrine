#!/usr/bin/env bash
# Publish a static dist/ tree to here.now workspace ottplay (player.ottplay.here.now).
# Site slug for player.ottplay.here.now label: liminal-sketch-vv8r
set -euo pipefail

DIST="${1:-./dist}"
WORKSPACE="${HERENOW_WORKSPACE:-ottplay}"
CLIENT="${HERENOW_CLIENT:-github-actions/ottplay-web-vitrine}"
SLUG="${HERENOW_SITE_SLUG:-}"
OVERWRITE="${OVERWRITE:-0}"
SPA="${SPA:-1}"

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

if [[ -z "${HERENOW_API_KEY:-}" && ! -f "${HOME}/.herenow/credentials" ]]; then
  echo "error: set HERENOW_API_KEY or write ~/.herenow/credentials" >&2
  exit 1
fi

if [[ -n "${HERENOW_API_KEY:-}" ]]; then
  mkdir -p "${HOME}/.herenow"
  printf '%s' "$HERENOW_API_KEY" > "${HOME}/.herenow/credentials"
  chmod 600 "${HOME}/.herenow/credentials"
fi

PUBLISH_SH="${HERENOW_PUBLISH_SCRIPT:-$PWD/.ci-tools/herenow/here-now/scripts/publish.sh}"
if [[ ! -x "$PUBLISH_SH" ]]; then
  echo 'Check out heredotnow/skill at 8cf033ed53b82c0c67b16359c8c431f99e111d04 into .ci-tools/herenow first.' >&2
  exit 1
fi
PUBLISH_ROOT="$(cd "$(dirname "$PUBLISH_SH")/../.." && pwd)"
if [[ "$(git -C "$PUBLISH_ROOT" rev-parse HEAD)" != '8cf033ed53b82c0c67b16359c8c431f99e111d04' ]]; then
  echo 'Publisher revision differs from the reviewed CI pin.' >&2
  exit 1
fi
if [[ -n "$(git -C "$PUBLISH_ROOT" status --porcelain --untracked-files=no)" ]]; then
  echo 'Publisher checkout contains local modifications.' >&2
  exit 1
fi

ARGS=( "$DIST" --workspace "$WORKSPACE" --client "$CLIENT" )
if [[ -n "$SLUG" ]]; then
  ARGS+=( --slug "$SLUG" )
fi
if [[ "$OVERWRITE" == "1" || "$OVERWRITE" == "true" ]]; then
  ARGS+=( --overwrite )
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
"$PUBLISH_SH" "${ARGS[@]}"
