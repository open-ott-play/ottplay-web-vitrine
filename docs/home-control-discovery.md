# Home command-server discovery runbook

Use this runbook to connect a player, update the public Home catalog, diagnose a
failed discovery, or roll back a catalog change. The catalog is a static file;
the command server remains a separate service.

## Addresses and prerequisites

- Player: <https://player.ottplay.here.now/>.
- Current public catalog: <https://player.ottplay.here.now/local/control-discovery.0a3bf94cadba71fdd02af4783574ea4690de2839a3eaa05a2bea18a715eb2e4d.json>.
- Home **Server address**: `https://www.2560801.xyz/ott-control`.

Do not enter the catalog URL in the player's **Server address** field. The
catalog contains only the reviewed DNS-SD identity and HTTPS address:

```json
{
  "servers": [
    {
      "address": "https://www.2560801.xyz/ott-control",
      "domain": "alvit.cf",
      "id": "home._ottplay-ctrl._tcp.alvit.cf."
    }
  ],
  "version": 1
}
```

Reading this file neither checks the controller's current availability nor
grants remote control. Pairing, device credentials and command queues remain on
the Home controller. Its configured `public_url` and current DNS-SD descriptor
must agree with the catalog when it validates pairing.

No installation is needed to open the hosted player. For local Chrome/server,
Tauri or Capacitor, install the appropriate [official player release](https://github.com/open-ott-play/ottplay-foss/releases)
using its platform instructions, and use a deployment profile that loads this
catalog before the player. This website repository does not update installed
applications. See the player's [command-server guide](https://github.com/open-ott-play/ottplay-foss/blob/main/docs/remote-command-server.md)
for native discovery, transport requirements and manual setup.

## Connect and use a player

1. Open the player. A compatible installation with no saved command-server
   settings attempts discovery once after its device identity is ready.
2. To retry deliberately, open **Settings → Remote control → Find command
   server** (shortcut **6** within that screen). One result starts pairing;
   multiple results require a selection. Read the requested controller address.
3. On the administrator's configured `ott` CLI, approve the code shown by that
   player. Replace both example values with the intended alias and displayed
   eight-character code:

   ```sh
   ott approve living-room A1B2C3D4
   ott living-room
   ```

   The second command reads the alias's available controls and player summary.
   Use the [CLI setup and command guide](https://github.com/open-ott-play/ottplay-control-server/blob/main/docs/cli.md)
   if the administrator's CLI is not installed or configured yet.
4. Check the player's connection status. An approved response must match this
   device and the selected address before settings are saved. **Cancel pairing**
   stops a pending attempt; an expired code requires a new attempt.

Existing connections and deliberate disconnects are preserved. A catalog update
does not replace a saved address/token or silently reconnect a disabled player.
**Find command server** can stage a deliberate replacement, but the saved
connection changes only after successful approval. For manual setup, enter the
Home server address above and that device's own access code; never an
administrator token, another player's code, or credentials embedded in a URL.

## How the deployment profile selects the catalog

The generated synchronous `local/hosted.js` sets:

```js
window.__OTT_CONTROL_DISCOVERY_URL__ = "https://player.ottplay.here.now/local/control-discovery.0a3bf94cadba71fdd02af4783574ea4690de2839a3eaa05a2bea18a715eb2e4d.json";
```

`scripts/swop.py` inserts that script before other player scripts and includes
the complete profile SHA-256 in its script URL. It requires the upstream
`window.__OTT_CONTROL_DISCOVERY_VERSION__ = 1` capability marker as well as the
existing hosted/SWOP/EPG capabilities. A marker is a compatibility check, not
proof that a particular TV's browser has passed transport testing.

The absolute catalog URL supports deployments on other origins; it needs
credentialless CORS there. Native DNS discovery is a separate player feature.
An unconfigured generic player without this deployment profile does not inherit
the personal Home catalog merely because it is installed on the same computer.

## Update the catalog and publish an accepted beta

1. Review a public descriptor from the intended controller. Keep exactly
   `version: 1` and `servers`, with at most 16 distinct `{id, domain, address}`
   entries. Do not copy a private controller configuration. Changing the
   nominated address is a trust-boundary change, even when the schema is valid.
2. Serialize the reviewed value using
   `(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()`.
   Hash those exact bytes with SHA-256 and save them as
   `static/local/control-discovery.<sha256>.json`. Update
   `CONTROL_DISCOVERY_SHA256` in `scripts/swop.py` and the assignment in
   `static/local/hosted.js`. Replace the previous current file in source; the
   stock source must contain exactly one catalog. Never change bytes behind an
   existing fingerprint or create a mutable discovery alias.
3. Run the focused checks, then the required repository checks:

   ```sh
   python3 scripts/swop.py static
   python3 -m unittest discover -s tests -p 'test_swop.py'
   python3 -m unittest discover -s tests -p 'test_retain_runtime.py'
   python3 -m unittest discover -s tests -p 'test_prepare_dist.py'
   ```

   See [local check prerequisites](release-workflow.md#local-checks). The
   validator rejects extra fields, duplicate keys, unsafe addresses, mismatched
   hashes and noncanonical JSON. A catalog change changes the profile hash;
   unchanged upstream bytes retain the same EPG graph hash.
4. Merge the reviewed source change. Select an **already published** official
   `vX.Y.Z-beta.N` release and its independently accepted manifest digest. Follow
   the [qualified beta contract](release-workflow.md#explicit-qualified-beta-deployment);
   a local build, successful build job or computed download hash alone is not
   release acceptance. Stage without publishing:

   ```sh
   python3 scripts/prepare-dist.py "$ACCEPTED_BETA_TAG" "$ACCEPTED_BETA_MANIFEST_SHA256" beta
   python3 scripts/swop.py ./dist --runtime
   ```

5. Save the current authenticated owner inventory, site version, settings and
   access configuration. Reconcile drift and create the
   [owner-verified publication copy](release-workflow.md#explicit-qualified-beta-deployment).
   Keep `REVIEWED_PRODUCTION_VERSION_ID` fixed to that reviewed snapshot; do not
   replace it automatically if publication reports a conflict. Check the exact
   copy in browsers, including a fresh unconfigured player with mocked pairing
   for offline tests. Keep EPG, SWOP, demo and MSX acceptance intact.
6. Request the existing workflow's read-only qualification from `main`:

   ```sh
   gh workflow run publish-herenow.yml --ref main \
     -f release_channel=beta \
     -f tag="$ACCEPTED_BETA_TAG" \
     -f manifest_sha256="$ACCEPTED_BETA_MANIFEST_SHA256" \
     -f expected_version_id="$REVIEWED_PRODUCTION_VERSION_ID" \
     -f qualify_only=true
   ```

   After accepted results and a fresh drift check, the same workflow inputs
   with `qualify_only=false` request publication through the protected production
   environment. Review the exact tag, digest and expected version in the run
   title. The wrapper requires `HERENOW_EXPECTED_VERSION` for local publication,
   rejects `OVERWRITE`, and checks the current owner version before its
   compare-and-swap update. A conflict requires new review, not a forced retry.

The publication copy retains older owner-verified catalog URLs, even though the
stock source has only the current file. It permits at most **16 catalog files,
8192 bytes each**. The final union of old and new EPG graphs permits **32 graphs,
2 MB per file, 8 MB per graph, 32 MB total**; identical shared paths count once.
Exceeding a bound stops publication before graph downloads/writes. There is no
automatic pruning. Existing `.herenow/`, demo, MSX and `local/swop.json` protections
still apply; reconcile unexplained differences instead of overwriting them.

## Verify the published result

Compare the complete owner inventory with the accepted publication receipt and
check that site settings/access are unchanged. Keep the pre-update receipt for
rollback. Reload a test player to load its new entry page and profile; an
already-open player can legitimately continue using its previous catalog URL.

Run this public read-only probe from the repository checkout, using the verified OpenSSL 3 curl from the
[host TLS profile](security-design.md#host-tls-profile), without authorization:

```sh
DISCOVERY_URL='https://player.ottplay.here.now/local/control-discovery.0a3bf94cadba71fdd02af4783574ea4690de2839a3eaa05a2bea18a715eb2e4d.json'
curl --disable --fail --silent --show-error --max-time 15 --max-filesize 8192 \
  --proto '=https' --tlsv1.2 --ciphers 'DEFAULT:@SECLEVEL=2' \
  -H 'Origin: http://localhost:8443' \
  -D discovery.headers -o discovery.json "$DISCOVERY_URL"
python3 - <<'PY'
import hashlib, sys
from pathlib import Path
sys.path.insert(0, "scripts")
from swop import CONTROL_DISCOVERY_SHA256, validate_control_discovery
raw = Path("discovery.json").read_bytes()
validate_control_discovery(raw, CONTROL_DISCOVERY_SHA256)
print("Verified public catalog SHA-256:", hashlib.sha256(raw).hexdigest())
PY
```

Run the probe from the source revision matching the catalog being checked; after
a catalog change use its new URL and pin. Expect HTTP 200, JSON content type,
the exact fingerprinted bytes and `Access-Control-Allow-Origin: *`. Repeat for
the actual target origin, including `tauri://localhost`, `capacitor://localhost`
or `null` where applicable. In developer tools on a target player page that has
the deployment profile, test a real browser fetch as well:

```javascript
const response = await fetch(window.__OTT_CONTROL_DISCOVERY_URL__, {
  credentials: "omit",
  redirect: "error"
});
console.log(response.status, response.headers.get("content-type"), await response.json());
```

Headers alone do not prove browser CORS behavior. Do not send credentials or use
a cache-busting URL. Test normal caching and retain prior URLs: JSON and HTML
can have different cache lifetimes and edge caches can converge at different times.

## Troubleshooting

- **The file returns HTTP 200, but discovery fails:** check MIME and exact bytes.
  A missing static path can return the site's HTML shell with HTTP 200. Compare
  the loaded profile's URL with the published manifest filename and hash.
- **Works on here.now, fails locally or on a TV:** test CORS from that exact
  origin and inspect the player's transport error. An older browser without
  the required secure discovery transport needs an updated player/browser or
  explicit manual setup. Same-origin success is insufficient evidence for
  Tauri, Capacitor or LG.
- **Catalog is correct, pairing is unavailable:** the catalog is not a health
  check. Verify the controller is reachable and its `public_url`/DNS-SD identity
  still match the descriptor. Check the selected alias/code on the administrator
  CLI; cancel an expired attempt and retry deliberately.
- **An existing player did not switch servers:** this is expected. Saved
  connection settings and deliberate disconnects take priority. Use **Find
  command server** only when replacement is intended; do not clear all settings.
- **Old catalog still appears after deployment:** inspect the loaded profile
  and reload a test player. Do not edit an old fingerprinted file. If the
  published new profile is wrong, use the rollback procedure below.
- **Publication stops on version, protected-file or capacity checks:** stop and
  inspect the owner snapshot and receipt. Preserve concurrent work. Do not
  enable overwrite, remove old URLs, or increase byte limits simply to bypass
  the failure; reconcile the exact difference in a reviewed change.

## Roll back safely

For a catalog-only mistake, prefer a forward correction: restore the previously
reviewed catalog and profile pin in source, review/merge it, then publish an
accepted player with the normal current-owner CAS guard. Retention keeps both
old and new catalog URLs available. Already connected players retain their
settings; reverting a catalog does not revoke issued device credentials.

For an emergency whole-site rollback, first stop competing publishers and save
the current owner inventory/version/settings. Review the intended earlier
version through [here.now version history](https://here.now/docs#versions) and
restore only that verified version using the owner's version controls. This is
a whole-site change: it can revert player/EPG/proxy assets and omit URLs added
later. Check settings/access and exact restored inventory afterward, reload
affected test players, and repeat discovery plus ordinary playback/EPG/SWOP
acceptance. Keep the displaced version's receipt so recovery remains possible.
Do not claim a catalog rollback also rolls back the separate controller.
