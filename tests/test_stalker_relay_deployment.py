"""Offline synthetic OCI/manifest checks; never contact a registry or cluster."""
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'deploy/stalker-relay/render.py'
SPEC = importlib.util.spec_from_file_location('stalker_render', SCRIPT)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)
SOURCE = 'a' * 40
TAG = 'v1.2.3-beta.1'
VERSION = TAG[1:]


def encoded(value):
    return json.dumps(value, separators=(',', ':'), sort_keys=True).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class Fixture:
    def __init__(self, directory, *, arch='arm64', user='nonroot', source=SOURCE,
                 version=VERSION, duplicate_arm=False, other_arch=False, extra=None):
        self.directory = Path(directory)
        blobs = {}

        def blob(value, media_type):
            raw = value if isinstance(value, bytes) else encoded(value)
            identity = digest(raw)
            blobs['blobs/sha256/' + identity] = raw
            return {'mediaType': media_type, 'digest': 'sha256:' + identity, 'size': len(raw)}

        config = blob({'architecture': arch, 'os': 'linux', 'config': {'User': user, 'Labels': {
            'org.opencontainers.image.version': version, 'org.opencontainers.image.revision': source,
        }}}, 'application/vnd.oci.image.config.v1+json')
        layer = blob(b'fixture layer: not an executable image', 'application/vnd.oci.image.layer.v1.tar')
        image = blob({'schemaVersion': 2, 'mediaType': renderer.MANIFEST, 'config': config, 'layers': [layer]}, renderer.MANIFEST)
        self.image_digest = image['digest']
        children = [image]
        if duplicate_arm:
            children.append(blob({'schemaVersion': 2, 'mediaType': renderer.MANIFEST, 'config': config,
                                  'layers': [layer], 'annotations': {'fixture': 'second'}}, renderer.MANIFEST))
        if other_arch:
            amd_config = blob({'architecture': 'amd64', 'os': 'linux', 'config': {'User': 'nonroot'}}, 'application/vnd.oci.image.config.v1+json')
            children.append(blob({'schemaVersion': 2, 'mediaType': renderer.MANIFEST, 'config': amd_config, 'layers': [layer]}, renderer.MANIFEST))
            attestation_config = blob({'architecture': 'unknown', 'os': 'unknown'}, 'application/vnd.oci.image.config.v1+json')
            children.append(blob({'schemaVersion': 2, 'mediaType': renderer.MANIFEST, 'config': attestation_config,
                                  'layers': [], 'annotations': {'vnd.docker.reference.type': 'attestation-manifest'}}, renderer.MANIFEST))
        index = blob({'schemaVersion': 2, 'mediaType': renderer.INDEX, 'manifests': children}, renderer.INDEX)
        blobs['index.json'] = encoded({'schemaVersion': 2, 'manifests': [index]})
        blobs['oci-layout'] = encoded({'imageLayoutVersion': '1.0.0'})
        self.archive = self.directory / 'fixture.oci.tar'
        with tarfile.open(self.archive, 'w') as tar:
            for name, raw in blobs.items():
                info = tarfile.TarInfo(name); info.size = len(raw)
                tar.addfile(info, io.BytesIO(raw))
            if extra:
                tar.addfile(extra)
        self.manifest = self.directory / 'release-manifest.json'
        self.release = {'schema': 1, 'repository': 'open-ott-play/ottplay-foss', 'source_sha': SOURCE,
                        'tag': TAG, 'assets': [{'name': renderer.ASSET, 'size': self.archive.stat().st_size,
                                               'sha256': digest(self.archive.read_bytes())}],
                        'package_versions': [{'path': renderer.ASSET, 'format': 'oci',
                                              'field': ['config', 'Labels', 'org.opencontainers.image.version'],
                                              'version': VERSION, 'images': [{'os': 'linux', 'architecture': 'arm64',
                                              'version': VERSION, 'config_sha256': config['digest'][7:]}]}]}
        self.save()

    def save(self):
        self.manifest.write_bytes(encoded(self.release))
        self.manifest_hash = digest(self.manifest.read_bytes())

    def verify(self, **overrides):
        values = {'manifest_path': self.manifest, 'manifest_sha256': self.manifest_hash,
                  'archive_path': self.archive, 'source': SOURCE, 'tag': TAG}
        values.update(overrides)
        return renderer.verify(**values)


class StalkerRelayDeployment(unittest.TestCase):
    def test_verified_arm64_leaf_renders_concrete_least_privilege_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            identity = f.verify()
            self.assertEqual(identity['digest'], f.image_digest)
            output = json.loads(renderer.render(identity, 'registry.example/ottplay-stalker-relay',
                                                'relay-config-v1', '100m', '128Mi', '512Mi'))
            deployment, service, egress, ingress = output['items']
            self.assertEqual(deployment['spec']['replicas'], 1)
            self.assertEqual(deployment['spec']['strategy'], {'type': 'Recreate'})
            pod = deployment['spec']['template']['spec']
            container = pod['containers'][0]
            self.assertEqual(container['image'], 'registry.example/ottplay-stalker-relay@' + f.image_digest)
            self.assertEqual(container['command'], ['/app/ottplay-server'])
            self.assertEqual(container['args'], ['--host', '0.0.0.0', '--port', '8080', '--stalker-relay-only',
                                                '--stalker-relay-config', '/secret/config.json'])
            self.assertNotIn('env', container)
            self.assertFalse(pod['automountServiceAccountToken'])
            self.assertEqual(pod['securityContext']['fsGroup'], 65532)
            self.assertEqual(pod['volumes'][0]['secret']['defaultMode'], 0o440)
            self.assertTrue(container['volumeMounts'][0]['readOnly'])
            self.assertEqual(service['spec']['type'], 'ClusterIP')
            self.assertEqual(egress['spec']['egress'][0]['to'][0]['podSelector']['matchLabels'], {'app': 'ottplay-stalker-relay'})
            self.assertEqual(ingress['spec']['ingress'][0]['from'][0]['podSelector']['matchLabels'], {'app': 'cloudflared', 'tunnel': 'h7'})
            self.assertNotIn('@@', json.dumps(output))

    def test_multi_platform_index_selects_only_runnable_arm64_leaf(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory, other_arch=True)
            self.assertEqual(f.verify()['digest'], f.image_digest)

    def test_internal_blob_hash_is_checked_even_after_archive_hash_is_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            with tarfile.open(f.archive, 'r:') as archive:
                entries = [(member, archive.extractfile(member).read()) for member in archive]
            changed = False
            with tarfile.open(f.archive, 'w') as archive:
                for member, data in entries:
                    if not changed and member.name.startswith('blobs/'):
                        data = bytes([data[0] ^ 1]) + data[1:]
                        changed = True
                    archive.addfile(member, io.BytesIO(data))
            f.release['assets'][0]['sha256'] = digest(f.archive.read_bytes())
            f.save()
            with self.assertRaisesRegex(ValueError, 'blob checksum'):
                f.verify()

    def test_pinned_manifest_archive_and_declared_source_are_all_required(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            for overrides in [{'manifest_sha256': 'b' * 64}, {'source': 'b' * 40}, {'tag': 'v1.2.3-rc.1'}]:
                with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                    f.verify(**overrides)
            with f.archive.open('r+b') as stream:
                stream.seek(1000); stream.write(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                f.verify()

    def test_actual_platform_user_and_labels_must_match_release_receipt(self):
        for override in [{'arch': 'amd64'}, {'user': 'root'}, {'source': 'b' * 40},
                         {'version': '9.9.9'}, {'duplicate_arm': True}]:
            with self.subTest(override=override), tempfile.TemporaryDirectory() as directory:
                f = Fixture(directory, **override)
                with self.assertRaises(ValueError):
                    f.verify()

    def test_config_receipt_cannot_point_at_unrelated_blob(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            f.release['package_versions'][0]['images'][0]['config_sha256'] = 'b' * 64
            f.save()
            with self.assertRaisesRegex(ValueError, 'config differs'):
                f.verify()

    def test_archive_links_and_path_entries_rejected_without_extraction(self):
        for name, kind in [('../escape', tarfile.REGTYPE), ('symlink', tarfile.SYMTYPE),
                           ('index.json', tarfile.REGTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                info = tarfile.TarInfo(name); info.type = kind; info.linkname = '/outside'
                f = Fixture(directory, extra=info)
                with self.assertRaises(ValueError):
                    f.verify()
                self.assertFalse((Path(directory).parent / 'escape').exists())

    def test_no_duplicate_json_fields_or_missing_version_receipt(self):
        with self.assertRaises(ValueError):
            renderer.read_json(b'{"schema":1,"schema":2}')
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory); f.release['package_versions'] = []; f.save()
            with self.assertRaisesRegex(ValueError, 'version receipt'):
                f.verify()

    def test_resource_values_and_repository_are_explicit_and_not_interpolated(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory); identity = f.verify()
            defaults = ['registry.example/relay', 'config-v1', '100m', '128Mi', '512Mi']
            for index, value in [(0, 'registry.example/relay:latest'), (0, 'bad"image'),
                                 (1, '../secret'), (2, '0m'), (3, '0Mi'), (4, '64Mi')]:
                args = defaults.copy(); args[index] = value
                with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                    renderer.render(identity, *args)

    def test_cli_never_overwrites_existing_output_or_writes_on_invalid_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            output = Path(directory) / 'manifest.json'
            args = [sys.executable, str(SCRIPT), '--manifest', str(f.manifest), '--manifest-sha256', f.manifest_hash,
                    '--oci', str(f.archive), '--source', SOURCE, '--tag', TAG, '--repository', 'registry.example/relay',
                    '--secret', 'relay-config-v1', '--cpu-request', '100m', '--memory-request', '128Mi',
                    '--memory-limit', '512Mi', '--output', str(output)]
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            before = output.read_bytes()
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_bytes(), before)
            self.assertIn('Output already exists', result.stderr)
            self.assertNotIn(str(output), result.stderr)
            output.unlink(); f.manifest.write_text('{}')
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
            self.assertIn('Release manifest checksum mismatch', result.stderr)
            self.assertNotIn('Traceback', result.stderr)

    def test_malformed_manifest_shapes_return_safe_errors_without_tracebacks(self):
        for field in ['assets', 'package_versions', 'images']:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                f = Fixture(directory)
                if field == 'images':
                    f.release['package_versions'][0]['images'] = ['PRIVATE_INPUT_MARKER']
                else:
                    f.release[field] = ['PRIVATE_INPUT_MARKER']
                f.save()
                output = Path(directory) / 'manifest.json'
                args = [sys.executable, str(SCRIPT), '--manifest', str(f.manifest), '--manifest-sha256', f.manifest_hash,
                        '--oci', str(f.archive), '--source', SOURCE, '--tag', TAG, '--repository', 'registry.example/relay',
                        '--secret', 'relay-config-v1', '--cpu-request', '100m', '--memory-request', '128Mi',
                        '--memory-limit', '512Mi', '--output', str(output)]
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('invalid structure', result.stderr)
                self.assertNotIn('PRIVATE_INPUT_MARKER', result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertFalse(output.exists())

    def test_documented_secret_creation_preserves_existing_version(self):
        guide = (ROOT / 'docs/stalker-relay.md').read_text()
        section = guide.split('Create a versioned Secret', 1)[1]
        command = re.search(r'```sh\n(.*?)\n```', section, re.S).group(1)
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source = directory / 'private.json'
            state = directory / 'saved-secret'
            source.write_text('new synthetic config')
            executable = directory / 'kubectl'
            executable.write_text('#!' + sys.executable + "\n" + r'''import os
import pathlib
import sys
args = sys.argv[1:]
state = pathlib.Path(os.environ['TEST_SECRET_STATE'])
if 'create' in args:
    filename = next(item.split('=', 2)[2] for item in args if item.startswith('--from-file=config.json='))
    content = pathlib.Path(filename).read_text()
    if '--dry-run=client' in args:
        print(content)
    else:
        try:
            with state.open('x') as output:
                output.write(content)
        except FileExistsError:
            sys.exit(1)
elif 'apply' in args:
    state.write_text(sys.stdin.read().strip())
else:
    sys.exit(2)
''')
            executable.chmod(0o700)
            env = {**os.environ, 'PATH': str(directory) + os.pathsep + os.environ.get('PATH', ''),
                   'SECRET_NAME': 'fixture-config-v2', 'PRIVATE_CONFIG': str(source),
                   'TEST_SECRET_STATE': str(state)}
            state.write_text('retained synthetic config')
            existing = subprocess.run(['bash', '-e'], input=command, text=True, capture_output=True, env=env, check=False)
            self.assertNotEqual(existing.returncode, 0)
            self.assertEqual(state.read_text(), 'retained synthetic config')
            state.unlink()
            fresh = subprocess.run(['bash', '-e'], input=command, text=True, capture_output=True, env=env, check=False)
            self.assertEqual(fresh.returncode, 0, fresh.stderr)
            self.assertEqual(state.read_text(), 'new synthetic config')
            self.assertEqual(fresh.stdout, '')

    def test_raw_template_cannot_be_applied_as_json(self):
        with self.assertRaises(json.JSONDecodeError):
            json.loads(SCRIPT.with_name('manifest.json.in').read_text())


if __name__ == '__main__':
    unittest.main()
