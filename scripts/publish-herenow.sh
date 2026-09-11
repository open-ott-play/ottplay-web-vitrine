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

PUBLISH_SH="${HERENOW_PUBLISH_SCRIPT:-}"
if [[ -z "$PUBLISH_SH" || ! -x "$PUBLISH_SH" ]]; then
  PUBLISH_SH=""
  for candidate in \
    "${HOME}/.claude/skills/here-now/scripts/publish.sh" \
    "${HOME}/.agents/skills/here-now/scripts/publish.sh" \
    "${HOME}/.hermes/skills/here-now/scripts/publish.sh" \
    "${HOME}/.cursor/skills/here-now/scripts/publish.sh" \
    "./scripts/here-now-publish.sh"
  do
    if [[ -x "$candidate" ]]; then
      PUBLISH_SH="$candidate"
      break
    fi
  done
fi

if [[ -z "$PUBLISH_SH" ]]; then
  FOUND=$(find "${HOME}" -path '*here-now*/scripts/publish.sh' 2>/dev/null | head -1 || true)
  if [[ -n "$FOUND" && -f "$FOUND" ]]; then
    chmod +x "$FOUND" || true
    PUBLISH_SH="$FOUND"
  fi
fi

if [[ -z "$PUBLISH_SH" ]]; then
  echo "Installing here-now skill…" >&2
  npx --yes skills add heredotnow/skill --skill here-now -g
  for candidate in \
    "${HOME}/.claude/skills/here-now/scripts/publish.sh" \
    "${HOME}/.agents/skills/here-now/scripts/publish.sh"
  do
    if [[ -x "$candidate" ]]; then PUBLISH_SH="$candidate"; break; fi
  done
fi

if [[ -z "$PUBLISH_SH" || ! -x "$PUBLISH_SH" ]]; then
  echo "error: here-now publish.sh not found after install" >&2
  find "${HOME}" -path '*here-now*/publish.sh' 2>/dev/null | head -20 >&2 || true
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
