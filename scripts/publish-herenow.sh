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

echo "Publishing $DIST → workspace=$WORKSPACE slug=${SLUG:-'(create/new)'} via $PUBLISH_SH …" >&2
"$PUBLISH_SH" "${ARGS[@]}"
