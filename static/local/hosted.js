window.__OTTPLAY_HOSTED__ = {
  "version": 1,
  "controlTransport": {
    "version": 1,
    "endpoint": "/ott-control/api/device-transport",
    "bases": [
      "https://player.ottplay.here.now/ott-control",
      "https://www.2560801.xyz/ott-control"
    ]
  },
  "epg": {
    "mode": "server",
    "apiBase": "/epg/v1",
    "sourceId": "epg-one",
    "serverWorkerUrl": "/hosted/epg-server.js",
    "diagnosticsUrl": "/hosted/epg-diagnostics.js",
    "source": "https://cdn.epg.one/epg2.xml.gz",
    "workerUrl": "/hosted/epg-worker.js",
    "refreshMs": 7200000
  },
  "swop": {
    "transport": "herenow",
    "collection": "swop_pairs",
    "entryUrl": "/swop-input/"
  },
  "vportal": {
    "routes": [
      {
        "upstream": "http://cd3c21307c36.vportalu.net/api/v1/",
        "path": "/vportal/provider-1"
      }
    ]
  },
  "stalker": {
    "routes": [
      {
        "upstream": "http://vpn.vivioelectronics.top/server/load.php",
        "path": "/stalker/provider-1"
      },
      {
        "upstream": "http://nk.team-tx.st/server/load.php",
        "path": "/stalker/provider-2"
      },
      {
        "upstream": "http://z1.babo01.com:8080/server/load.php",
        "path": "/stalker/provider-3"
      }
    ]
  }
};
window.__OTT_CONTROL_DISCOVERY_URL__ = "https://player.ottplay.here.now/local/control-discovery.0a3bf94cadba71fdd02af4783574ea4690de2839a3eaa05a2bea18a715eb2e4d.json";
