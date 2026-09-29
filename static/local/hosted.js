window.__OTTPLAY_HOSTED__ = {
  "version": 1,
  "epg": {
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
  }
};
window.__OTT_CONTROL_DISCOVERY_URL__ = "https://www.2560801.xyz/ott-control/api/discovery";
