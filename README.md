# ottplay-web-vitrine

Public **static** OttPlay FOSS web player — a thin vitrine that publishes the player UI to [here.now](https://here.now), same role as [`inverter-web-vitrine`](https://github.com/victron-venus/inverter-web-vitrine) for Victron status.

**Live demo:** https://player.ottplay.here.now/

Upstream player builds: [`open-ott-play/ottplay-foss`](https://github.com/open-ott-play/ottplay-foss) (release asset `ottplay-foss-dist.tar.gz`).

<!-- ci-release-process:start -->
## CI and deployment

See [CI and deployment workflow](docs/release-workflow.md) for required checks and local commands. This repository uses validation-only policy; application release channels do not apply.
<!-- ci-release-process:end -->

## Why this exists

Desktop / Cap / Tauri installs are great for daily use. This repo answers:

> Can someone **try the full FOSS player in a browser** without cloning or paying for App Store / Play?

Yes — publish the static frontend to a stable here.now workspace URL and explicitly approve updates from qualified stable player releases.

## What it is

| Layer | Role |
| --- | --- |
| Browser | Static OttPlay UI (`index.html` + `stbPlayer.js` + assets) |
| here.now workspace `ottplay` | Hosts the Site at label **`player`** → `https://player.ottplay.here.now/` |
| ottplay-foss releases | Source of truth for the built web bundle |

```text
approved ottplay-foss stable release  →  verify and unpack ottplay-foss-dist.tar.gz
                           →  here.now publish (workspace ottplay)
                           →  https://player.ottplay.here.now/
```

## Limits (static host)

- **No** `ottplay-server` / HLS proxy / command-queue on here.now.
- Playlists and EPG must be **public HTTPS** endpoints the browser can reach (CORS permitting).
- Same-origin companion APIs from Mode A are not available on this URL.

## How to update the live site

### Publish a reviewed stable release

1. Configure `HERENOW_API_KEY` in the protected production environment with access to the `ottplay` workspace. Review the workspace and site slug configured in the workflow.
2. Run **Actions → Publish here.now → Run workflow** from the default branch with an exact stable `vX.Y.Z` tag from `open-ott-play/ottplay-foss`.
3. Review the deployment and approve its production environment. The workflow verifies the stable archive against its RC manifest, source revision and successful validation run before publishing.

Release events and `repository_dispatch` do not publish the site. See the [operator runbook](docs/release-workflow.md) for the repository validation and deployment boundary.

### Prepare verified artifacts locally

In a fresh checkout with no existing `dist/`, run `python3 scripts/prepare-dist.py vX.Y.Z` for the selected stable player release. This verifies and stages the distribution without publishing. Inspect `dist/`; use the protected workflow above to update the live site.

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
