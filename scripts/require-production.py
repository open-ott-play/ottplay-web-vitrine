#!/usr/bin/env python3
"""Require a manually dispatched default-branch production approval boundary."""
import json, os, subprocess
repo = os.environ['GITHUB_REPOSITORY']
def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', 'repos/' + repo + path]))
info = api('')
if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch' or os.environ.get('GITHUB_REF') != 'refs/heads/' + info['default_branch']:
    raise SystemExit('Production deployment requires default-branch manual dispatch')
environment = api('/environments/production')
if not any(rule.get('type') == 'required_reviewers' and rule.get('reviewers') for rule in environment.get('protection_rules', [])):
    raise SystemExit('Configure required reviewers on production before deployment')
print('Manual production approval boundary verified')
