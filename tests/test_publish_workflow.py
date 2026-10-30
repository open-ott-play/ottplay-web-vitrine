"""Keep the protected deployment wired to the independently accepted manifest."""
from pathlib import Path
import unittest

import yaml


class PublishWorkflowContractTests(unittest.TestCase):
    def test_ready_generation_is_checked_immediately_before_production_publish(self):
        root = Path(__file__).parents[1]
        workflow = yaml.safe_load((root / ".github/workflows/publish-herenow.yml").read_text())
        steps = workflow["jobs"]["publish"]["steps"]
        publish = next(i for i, step in enumerate(steps) if "scripts/publish-herenow.sh" in step.get("run", ""))
        guard = steps[publish - 1]
        after = steps[publish + 1]
        self.assertEqual(guard["run"], "python3 scripts/check-public-epg.py ./dist --upstream")
        self.assertEqual(after["run"], "python3 scripts/check-public-epg.py ./dist")
        for step in (guard, after):
            self.assertNotIn("if", step)
            self.assertFalse(step.get("continue-on-error", False))

    def test_manifest_digest_is_required_and_passed_as_a_quoted_argument(self):
        root = Path(__file__).parents[1]
        workflow = yaml.safe_load((root / ".github/workflows/publish-herenow.yml").read_text())
        field = workflow["on"]["workflow_dispatch"]["inputs"]["manifest_sha256"]
        self.assertIs(field["required"], True)
        self.assertEqual(field["type"], "string")
        self.assertEqual(workflow["run-name"], "Publish ${{ inputs.tag }} · ${{ inputs.manifest_sha256 }} · from ${{ inputs.expected_version_id }}")
        job = workflow["jobs"]["publish"]
        self.assertEqual(job["environment"], "production")
        prepare = workflow["jobs"]["prepare"]
        self.assertNotIn("environment", prepare)
        steps = [step for step in prepare["steps"] if "scripts/prepare-dist.py" in step.get("run", "")]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["env"]["RELEASE_MANIFEST_SHA256"], "${{ inputs.manifest_sha256 }}")
        self.assertEqual(steps[0]["run"], 'python3 scripts/prepare-dist.py "$RELEASE_TAG" "$RELEASE_MANIFEST_SHA256" "$RELEASE_CHANNEL"')
        self.assertEqual(steps[0]["env"]["RELEASE_CHANNEL"], "${{ inputs.release_channel }}")
        channel = workflow["on"]["workflow_dispatch"]["inputs"]["release_channel"]
        self.assertEqual(channel, {"description": "Explicit deployment channel; beta requires independent qualification",
                                   "required": True, "type": "choice", "default": "stable", "options": ["stable", "beta"]})

    def test_browsers_and_publisher_receive_the_same_prepared_artifact(self):
        root = Path(__file__).parents[1]
        workflow = yaml.safe_load((root / ".github/workflows/publish-herenow.yml").read_text())
        prepare, browser, publish = (workflow["jobs"][name] for name in ("prepare", "browser", "publish"))
        self.assertEqual(browser["needs"], ["prepare"])
        self.assertEqual(publish["needs"], ["prepare", "browser"])
        self.assertNotIn("always()", publish["if"])
        self.assertNotIn("continue-on-error", browser)
        self.assertIs(browser["with"]["force-full"], True)
        self.assertEqual(browser["with"]["browser-artifact-id"], "${{ needs.prepare.outputs.artifact-id }}")
        self.assertEqual(browser["with"]["browser-release-channel"], "${{ inputs.release_channel }}")
        self.assertEqual(browser["with"]["browser-extended"], "${{ inputs.release_channel == 'stable' }}")
        self.assertIn("needs.prepare.outputs.manifest-sha256", browser["with"]["browser-prepare-command"])
        artifact = next(step for step in publish["steps"] if str(step.get("uses", "")).startswith("actions/download-artifact@"))
        self.assertEqual(artifact["with"]["artifact-ids"], browser["with"]["browser-artifact-id"])
        self.assertNotIn("name", artifact["with"])
        self.assertFalse(any("prepare-dist.py" in step.get("run", "") for step in publish["steps"]))
        verification = next(step for step in publish["steps"] if "browser_candidate.py unpack" in step.get("run", ""))
        self.assertEqual(verification["env"]["CANDIDATE_MANIFEST_SHA256"], "${{ needs.prepare.outputs.manifest-sha256 }}")
        self.assertFalse(any("secrets." in str(step) for step in prepare["steps"]))

    def test_read_only_qualification_cannot_enter_protected_publishing(self):
        root = Path(__file__).parents[1]
        workflow = yaml.safe_load((root / ".github/workflows/publish-herenow.yml").read_text())
        option = workflow["on"]["workflow_dispatch"]["inputs"]["qualify_only"]
        self.assertEqual(option["type"], "boolean")
        self.assertIs(option["default"], False)
        self.assertIn("!inputs.qualify_only", workflow["jobs"]["publish"]["if"])
        self.assertIn("github.event.repository.default_branch", workflow["jobs"]["publish"]["if"])
        self.assertEqual(workflow["jobs"]["publish"]["environment"], "production")

    def test_owner_version_is_required_and_reaches_the_real_publisher(self):
        root = Path(__file__).parents[1]
        workflow = yaml.safe_load((root / ".github/workflows/publish-herenow.yml").read_text())
        field = workflow["on"]["workflow_dispatch"]["inputs"]["expected_version_id"]
        self.assertIs(field["required"], True)
        self.assertEqual(field["type"], "string")
        publish = next(step for step in workflow["jobs"]["publish"]["steps"]
                       if "scripts/publish-herenow.sh" in step.get("run", ""))
        self.assertEqual(publish["env"]["HERENOW_EXPECTED_VERSION"], "${{ inputs.expected_version_id }}")
        self.assertNotIn("OVERWRITE", publish["env"])
        validation = yaml.safe_load((root / ".github/workflows/validate.yml").read_text())
        checkout = next(step for step in validation["jobs"]["validate"]["steps"]
                        if step.get("with", {}).get("repository") == "heredotnow/skill")
        self.assertEqual(checkout["with"]["ref"], "8cf033ed53b82c0c67b16359c8c431f99e111d04")
        self.assertIs(checkout["with"]["persist-credentials"], False)


if __name__ == "__main__":
    unittest.main()
