# Shared demo media

The files in `static/demo/` are the existing silent, 24-second synthetic H.264
test pattern (640×360), preserved byte-for-byte from the owned here.now site's
file API on 2026-09-27. `demo-media.json` records that source version, every
filename, size and SHA-256 hash. The media contains no provider or broadcast
content and is covered by this repository's MIT license.

The original generation recipe is
[`ottplay-foss/scripts/generate-demo-media.sh`](https://github.com/open-ott-play/ottplay-foss/blob/50434fda30631b0650c0ee0b121bed066bae1cc5/scripts/generate-demo-media.sh).
It generates the MP4 from FFmpeg's `testsrc2` filter and remuxes it into a VOD HLS
playlist and local segments. FFmpeg is only needed to regenerate or decode-check
the media; normal CI and deployments use the checked-in bytes offline.

`prepare-dist.py` verifies the stable archive, then adds this directory to the
staged site. It rejects an upstream `/demo/` collision instead of silently
overwriting verified player files. `publish-herenow.sh` checks the staged demo
again before using credentials or invoking the publisher, including direct
local invocations. An incomplete demo therefore cannot replace the live site.
The wrapper supplies explicit HLS MIME types to the pinned publisher's `file(1)`
fallback: `application/vnd.apple.mpegurl` for the playlist and `video/mp2t` for
the segments. Other files retain normal detection; the dependency stays pinned
and unmodified.

To change the demo, regenerate the complete set using the recipe above, replace
`static/demo/`, and update the sizes, hashes and provenance in `demo-media.json`
in the same reviewed change. Retain the `pattern.mp4` and `pattern.m3u8` URLs;
every segment referenced by the playlist must be included in the manifest.
The checker also rejects unlisted files, symlinks and remote segment references.
Never recover missing deployment inputs by downloading whatever is live during
a production run.

Run `bash scripts/ci.sh`, then decode both formats before accepting new media:

```sh
ffmpeg -v error -i static/demo/pattern.mp4 -f null -
ffmpeg -v error -i static/demo/pattern.m3u8 -f null -
```

Publishing remains a separate, manually approved production workflow. This
media maintenance procedure does not publish the player or change the stable
release verification requirements.
