# Documentation images

The README hero presents the same FOSS player that this repository publishes.
It reuses the upstream Studio 2026 interface screenshot with synthetic channels,
logos and programme data. This is an illustrative design capture, not a fresh
capture or acceptance record for the currently deployed web build.

- Original: [`studio-2026-default.png`](studio-2026-default.png), unchanged.
- Source: [OTT-play FOSS at commit 3429fea2](https://github.com/open-ott-play/ottplay-foss/blob/3429fea2/docs/images/studio-2026-default.png).
- Upstream terms: [MIT license](https://github.com/open-ott-play/ottplay-foss/blob/3429fea2/LICENSE).
- Rendered: [`hero.png`](hero.png), perspective and shadow only.

## Regenerate

Install ImageMagick 7, then run:

```sh
./scripts/render-docs-hero.sh
```

The script requires the 1280 × 720 original, applies a perspective transform and
rounded outer corners, and creates a neutral-background PNG. It does not redraw
text, controls or video. Keep the flat source for detailed reading. Review
provenance and the README caption when updating the screenshot; use public
fixtures instead of provider credentials or personal playlists.
