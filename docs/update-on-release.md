# Hook ottplay-foss releases → this vitrine

After `open-ott-play/ottplay-foss` uploads release assets (including `ottplay-foss-dist.tar.gz`), notify this repo so Actions republishes https://player.ottplay.here.now/.

## Secret in ottplay-foss

`WEB_VITRINE_PAT` — PAT/GitHub App token with `repo` scope on `open-ott-play/ottplay-web-vitrine`.

## Secret in ottplay-web-vitrine

`HERENOW_API_KEY` — here.now key with access to workspace `ottplay`.
Site slug for updates: `liminal-sketch-vv8r` (label `player`).

## Step for `.github/workflows/release.yml` (after assets uploaded)

```yaml
      - name: Notify web vitrine
        if: success()
        env:
          GH_TOKEN: ${{ secrets.WEB_VITRINE_PAT }}
        run: |
          if [ -z "$GH_TOKEN" ]; then
            echo "WEB_VITRINE_PAT unset — skip vitrine dispatch"
            exit 0
          fi
          gh api repos/open-ott-play/ottplay-web-vitrine/dispatches \
            -f event_type='ottplay-foss-release' \
            -f client_payload[tag]='${{ github.ref_name }}' \
            -f client_payload[repo]='${{ github.repository }}'
```

## Manual

Actions → **Publish here.now** → Run workflow.
