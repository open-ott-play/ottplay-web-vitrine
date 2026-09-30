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
        self.assertEqual(guard["run"], "python3 scripts/check-epg.py https://epg.2560801.xyz")
        self.assertNotIn("if", guard)
        self.assertFalse(guard.get("continue-on-error", False))

    def test_manifest_digest_is_required_and_passed_as_a_quoted_argument(self):
        root = Path(__file__).parents[1]
        workflow = yaml.safe_load((root / ".github/workflows/publish-herenow.yml").read_text())
        field = workflow["on"]["workflow_dispatch"]["inputs"]["manifest_sha256"]
        self.assertIs(field["required"], True)
        self.assertEqual(field["type"], "string")
        self.assertEqual(workflow["run-name"], "Publish ${{ inputs.tag }} · ${{ inputs.manifest_sha256 }}")
        job = workflow["jobs"]["publish"]
        self.assertEqual(job["environment"], "production")
        steps = [step for step in job["steps"] if "scripts/prepare-dist.py" in step.get("run", "")]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["env"]["RELEASE_MANIFEST_SHA256"], "${{ inputs.manifest_sha256 }}")
        self.assertEqual(steps[0]["run"], 'python3 scripts/prepare-dist.py "$RELEASE_TAG" "$RELEASE_MANIFEST_SHA256" "$RELEASE_CHANNEL"')
        self.assertEqual(steps[0]["env"]["RELEASE_CHANNEL"], "${{ inputs.release_channel }}")
        channel = workflow["on"]["workflow_dispatch"]["inputs"]["release_channel"]
        self.assertEqual(channel, {"description": "Explicit deployment channel; beta requires independent qualification",
                                   "required": True, "type": "choice", "default": "stable", "options": ["stable", "beta"]})


if __name__ == "__main__":
    unittest.main()
