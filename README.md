# ottplay-web-vitrine

Public **static** OttPlay FOSS web player — a thin vitrine that publishes the player UI to [here.now](https://here.now), same role as [`inverter-web-vitrine`](https://github.com/victron-venus/inverter-web-vitrine) for Victron status.

**Live demo:** https://player.ottplay.here.now/

Upstream player builds: [`open-ott-play/ottplay-foss`](https://github.com/open-ott-play/ottplay-foss) (release asset `ottplay-foss-dist.tar.gz`).

## Why this exists

Desktop / Cap / Tauri installs are great for daily use. This repo answers:

> Can someone **try the full FOSS player in a browser** without cloning or paying for App Store / Play?

Yes — publish the static frontend to a stable here.now workspace URL and refresh it on every player release.

## What it is

| Layer | Role |
| --- | --- |
| Browser | Static OttPlay UI (`index.html` + `stbPlayer.js` + assets) |
| here.now workspace `ottplay` | Hosts the Site at label **`player`** → `https://player.ottplay.here.now/` |
| ottplay-foss releases | Source of truth for the built web bundle |

```text
ottplay-foss tag/release  →  unpack ottplay-foss-dist.tar.gz
                           →  here.now publish (workspace ottplay)
                           →  https://player.ottplay.here.now/
```

## Limits (static host)

- **No** `ottplay-server` / HLS proxy / command-queue on here.now.
- Playlists and EPG must be **public HTTPS** endpoints the browser can reach (CORS permitting).
- Same-origin companion APIs from Mode A are not available on this URL.

## How to update the live site

### A. Automated (preferred)

1. Set repo secret **`HERENOW_API_KEY`** (here.now API key with access to workspace `ottplay`).
2. Optional secrets/vars: `HERENOW_WORKSPACE=ottplay`, `HERENOW_SITE_SLUG=liminal-sketch-vv8r`.
3. On each `ottplay-foss` GitHub Release (after assets upload), fire `repository_dispatch` type `ottplay-foss-release` at this repo (see [docs/update-on-release.md](docs/update-on-release.md)), **or** run **Actions → Publish here.now → Run workflow**.
4. The workflow downloads `ottplay-foss-dist.tar.gz`, extracts to `dist/`, and runs [`scripts/publish-herenow.sh`](scripts/publish-herenow.sh).

### B. Manual

```bash
# 1) Get a dist tree with index.html at the root
gh release download -R open-ott-play/ottplay-foss -p ottplay-foss-dist.tar.gz
mkdir -p dist && tar -xzf ottplay-foss-dist.tar.gz -C dist
# ensure dist/index.html exists (move nested folder up if needed)

# 2) here.now skill + key
npx skills add heredotnow/skill --skill here-now -g
mkdir -p ~/.herenow && chmod 700 ~/.herenow
# put API key in ~/.herenow/credentials (chmod 600) or export HERENOW_API_KEY

# 3) Publish into the ottplay workspace (update in place)
export HERENOW_WORKSPACE=ottplay
export HERENOW_SITE_SLUG=liminal-sketch-vv8r  # Site slug (label remains player)
./scripts/publish-herenow.sh ./dist
# if the live Site moved: OVERWRITE=1 ./scripts/publish-herenow.sh ./dist
```

Discover the Site slug (owner API key):

```bash
curl -sS -H "Authorization: Bearer $HERENOW_API_KEY" \
  -H "X-HereNow-Account: ottplay" \
  https://here.now/api/v1/publishes | jq .
```

Keep the **`player`** workspace label pointed at that Site (here.now dashboard / site-labels API). Updating the Site by `--slug` refreshes `https://player.ottplay.here.now/` without renaming the label.

## Secrets — never commit

| Name | Purpose |
| --- | --- |
| `HERENOW_API_KEY` | Authenticated publish into workspace `ottplay` |
| `HERENOW_SITE_SLUG` | Existing Site slug to `PUT` (update) instead of creating a new Site |
| `HERENOW_WORKSPACE` | Defaults to `ottplay` |

Do not commit `~/.herenow/credentials`, `.herenow/state.json`, or real `proxy.json`.

## Related

- Player source / releases: https://github.com/open-ott-play/ottplay-foss
- Privacy policy: https://github.com/open-ott-play/ottplay-foss/blob/main/docs/privacy-policy.md
- Pattern sibling: https://github.com/victron-venus/inverter-web-vitrine
- here.now docs: https://here.now/docs

## License

MIT
