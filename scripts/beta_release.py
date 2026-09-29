"""Additional checks for explicitly accepted beta bytes; never publish or rebuild.

The caller must first authenticate the raw manifest with the independently
accepted digest. Immutable Actions evidence is verified by local acceptance,
not downloaded with the production workflow's cross-repository token.
"""
import base64
import hashlib
import json
import re

REPO = "open-ott-play/ottplay-foss"
WORKFLOW = ".github/workflows/release-pipeline.yml"
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
BETA_TAG = re.compile(r"v(" + VERSION + r")-beta\.([1-9][0-9]*)", re.ASCII)


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


def positive(value):
    return type(value) is int and value > 0


def verify_run(run, source_sha):
    require(positive(run.get("id")) and positive(run.get("run_attempt")) and
            run.get("head_branch") == "main" and run.get("event") in ("push", "workflow_dispatch") and
            run.get("head_repository", {}).get("full_name") == REPO and
            run.get("head_sha") == source_sha,
            "Beta source must be a trusted main push or manual release run")


class BetaRelease:
    """Bind one beta release snapshot to its plan, policy and public assets."""

    def __init__(self, tag, manifest, raw_manifest, release, api):
        self.tag, self.api = tag, api
        self.manifest = manifest
        match = BETA_TAG.fullmatch(tag)
        require(match is not None, "An explicit canonical beta tag is required")
        base, sequence = match.groups()
        require(type(manifest.get("schema")) is int and manifest["schema"] == 1 and
                manifest.get("repository") == REPO and manifest.get("channel") == "beta" and
                manifest.get("tag") == tag and manifest.get("version") == base and
                manifest.get("workflow_path") == WORKFLOW,
                "Beta manifest channel, tag, base version or workflow mismatch")
        source = manifest.get("source_sha")
        require(isinstance(source, str) and re.fullmatch(r"[0-9a-f]{40}", source), "Invalid beta source SHA")
        require(positive(manifest.get("run_id")) and positive(manifest.get("run_attempt")),
                "Invalid beta source run provenance")
        plan = manifest.get("version_plan")
        keys = {"schema_version", "base_version", "version", "channel", "sequence", "tag",
                "source_sha", "build_number", "policy_sha256", "promotion"}
        require(isinstance(plan, dict) and set(plan) == keys and
                type(plan["schema_version"]) is int and plan["schema_version"] == 1 and
                plan["base_version"] == base and plan["version"] == tag[1:] and
                plan["channel"] == "beta" and type(plan["sequence"]) is int and
                plan["sequence"] == int(sequence) and plan["tag"] == tag and
                plan["source_sha"] == source and plan["promotion"] == "promote-bytes" and
                positive(plan["build_number"]) and plan["build_number"] <= 2_100_000_000 and
                manifest.get("plan_sha256") == digest(canonical(plan)),
                "Beta manifest differs from its exact frozen version plan")
        self.verify_policy(plan, source)
        self.expected = {}
        declarations = manifest.get("assets")
        require(isinstance(declarations, list) and 0 < len(declarations) <= 1000, "Invalid beta asset inventory")
        for asset in declarations:
            require(isinstance(asset, dict), "Invalid beta asset declaration")
            name = asset.get("name")
            require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) and
                    name != "release-manifest.json" and name not in self.expected and
                    positive(asset.get("size")) and isinstance(asset.get("sha256"), str) and
                    re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]), "Invalid or duplicate beta asset declaration")
            self.expected[name] = {"size": asset["size"], "digest": "sha256:" + asset["sha256"]}
        require("ottplay-foss-dist.tar.gz" in self.expected, "Beta manifest has no web distribution")
        self.expected["release-manifest.json"] = {"size": len(raw_manifest), "digest": "sha256:" + digest(raw_manifest)}
        self.snapshot = self.release_snapshot(release)

    def verify_policy(self, plan, source):
        snapshot = self.manifest.get("source_policy")
        require(isinstance(snapshot, dict) and type(snapshot.get("schema")) is int and
                snapshot["schema"] == 1 and snapshot.get("path") == ".release-policy.json",
                "Invalid beta source policy snapshot")
        content = self.api(f"contents/.release-policy.json?ref={source}")
        require(content.get("type") == "file" and content.get("encoding") == "base64" and
                positive(content.get("size")) and content["size"] <= 2_000_000,
                "Invalid beta source policy content")
        try:
            raw = base64.b64decode("".join(content["content"].split()), validate=True)
            policy = json.loads(raw)
        except (KeyError, ValueError, TypeError) as error:
            raise SystemExit("Invalid beta source policy encoding") from error
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        require(len(raw) == content["size"] and blob == content.get("sha") == snapshot.get("git_blob_sha") and
                digest(raw) == snapshot.get("sha256") and policy == snapshot.get("data") and
                isinstance(policy, dict) and policy.get("repository") == REPO and policy.get("mode") == "release" and
                policy.get("default_branch") == "main", "Beta source policy identity mismatch")
        require(all(policy.get(key, []) == [] for key in ("release_blockers", "stable_blockers")),
                "Beta source policy has unresolved qualification blockers")
        versioning = policy.get("versioning", {})
        require(isinstance(versioning, dict) and type(versioning.get("schema")) is int and
                versioning["schema"] == 1 and versioning.get("promotion") == plan["promotion"] and
                type(versioning.get("build_number_floor", 0)) is int and
                plan["build_number"] > versioning.get("build_number_floor", 0) and
                plan["policy_sha256"] == digest(canonical(policy)), "Beta plan policy binding mismatch")

    def release_snapshot(self, release):
        require(positive(release.get("id")) and release.get("tag_name") == self.tag and
                release.get("draft") is False and release.get("prerelease") is True,
                "Only the explicitly selected published beta prerelease can be deployed")
        inventory = {}
        for page in range(1, 12):
            assets = self.api(f"releases/{release['id']}/assets?per_page=100&page={page}")
            require(isinstance(assets, list), "Invalid beta release asset API response")
            for asset in assets:
                name = asset.get("name")
                require(name in self.expected and name not in inventory and positive(asset.get("id")) and
                        asset.get("state") == "uploaded" and type(asset.get("size")) is int and
                        asset["size"] == self.expected[name]["size"] and
                        asset.get("digest") == self.expected[name]["digest"],
                        "Beta release asset API inventory or digest differs from accepted manifest")
                inventory[name] = (asset["id"], asset["size"], asset["digest"])
            if len(assets) < 100:
                break
        require(set(inventory) == set(self.expected), "Beta release asset API inventory is incomplete")
        return release["id"], inventory

    def recheck(self):
        require(self.release_snapshot(self.api(f"releases/tags/{self.tag}")) == self.snapshot,
                "Beta release or asset identity changed during verification")

    def verify_web(self, stage):
        path = stage / "build-info.json"
        require(path.is_file() and path.stat().st_size <= 2_000_000, "Beta web build metadata is missing")
        build = json.loads(path.read_bytes())
        require(build.get("version") == self.tag[1:] and build.get("revision") == self.manifest["source_sha"] and
                build.get("bundleSha256") == digest((stage / "dist/player.js").read_bytes()),
                "Beta web version, source or player digest differs from accepted manifest")
