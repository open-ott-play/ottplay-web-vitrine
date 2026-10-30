#!/usr/bin/env python3
"""Verify a pinned official OCI release offline and render an ARM64 relay manifest."""
import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path

ASSET = 'ottplay-foss-container.oci.tar'
INDEX = 'application/vnd.oci.image.index.v1+json'
MANIFEST = 'application/vnd.oci.image.manifest.v1+json'
DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')
MAX_ARCHIVE = 2 * 1024 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(block)
    return digest.hexdigest()


def read_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, 'Duplicate JSON field')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs)
    require(isinstance(value, dict), 'Expected JSON object')
    return value


def verify(manifest_path, manifest_sha256, archive_path, source, tag):
    require(re.fullmatch(r'[0-9a-f]{64}', manifest_sha256), 'Expected pinned manifest SHA-256')
    require(re.fullmatch(r'[0-9a-f]{40}', source), 'Expected exact source revision')
    require(re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:beta|rc)\.[0-9]+)?', tag), 'Expected release tag')
    require(manifest_path.stat().st_size <= 8 * 1024 * 1024, 'Oversized release manifest')
    raw = manifest_path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == manifest_sha256, 'Release manifest checksum mismatch')
    release = read_json(raw)
    require(release.get('schema') == 1 and release.get('repository') == 'open-ott-play/ottplay-foss', 'Unexpected release manifest')
    require(release.get('source_sha') == source and release.get('tag') == tag, 'Release source/tag mismatch')
    assets = [a for a in release.get('assets', []) if a.get('name') == ASSET]
    require(len(assets) == 1, 'Expected exactly one official OCI asset')
    asset = assets[0]
    require(type(asset.get('size')) is int and 0 < asset['size'] <= MAX_ARCHIVE, 'Invalid OCI asset size')
    require(archive_path.stat().st_size == asset['size'], 'OCI archive size mismatch')
    with archive_path.open('rb') as stream:
        require(sha(stream) == asset.get('sha256'), 'OCI archive checksum mismatch')
    receipts = [r for r in release.get('package_versions', []) if r.get('path') == ASSET and r.get('format') == 'oci'
                and r.get('field') == ['config', 'Labels', 'org.opencontainers.image.version']]
    require(len(receipts) == 1, 'Missing official OCI version receipt')
    receipt = receipts[0]
    images = [i for i in receipt.get('images', []) if i.get('os') == 'linux' and i.get('architecture') == 'arm64']
    require(len(images) == 1 and isinstance(receipt.get('version'), str), 'Missing ARM64 version receipt')
    expected = images[0]
    require(expected.get('version') == receipt['version'], 'Conflicting OCI version receipt')
    # Official OCI assets are uncompressed tar; do not admit compressed bombs.
    with tarfile.open(archive_path, mode='r:') as archive:
        members = {}
        for number, member in enumerate(archive):
            require(number < 10000, 'Too many OCI entries')
            name = member.name
            while name.startswith('./'):
                name = name[2:]
            name = name.rstrip('/') if member.isdir() else name
            if member.isdir():
                require(name in {'', 'blobs', 'blobs/sha256'}, 'Unexpected OCI directory')
                continue
            require(member.isfile() and name not in members, 'Duplicate or nonregular OCI entry')
            require(0 <= member.size <= MAX_ARCHIVE, 'Oversized OCI entry')
            require(name in {'index.json', 'oci-layout'} or re.fullmatch(r'blobs/sha256/[0-9a-f]{64}', name), 'Unexpected OCI file')
            members[name] = member
            if name.startswith('blobs/'):
                with archive.extractfile(member) as stream:
                    require(sha(stream) == name.rsplit('/', 1)[1], 'OCI blob checksum mismatch')

        def document(name):
            member = members.get(name)
            require(member is not None and member.size <= 2 * 1024 * 1024, 'Missing or oversized OCI metadata')
            with archive.extractfile(member) as stream:
                return read_json(stream.read())

        def blob(descriptor, metadata=True):
            require(isinstance(descriptor, dict) and DIGEST.fullmatch(descriptor.get('digest', '')), 'Invalid OCI descriptor')
            name = 'blobs/sha256/' + descriptor['digest'][7:]
            require(name in members and type(descriptor.get('size')) is int and members[name].size == descriptor['size'], 'OCI descriptor size mismatch')
            return document(name) if metadata else None

        require(document('oci-layout').get('imageLayoutVersion') == '1.0.0', 'Unsupported OCI layout')
        selected = []
        visited = set()

        def walk(descriptor, depth=0):
            require(depth <= 8 and len(visited) < 1024, 'Oversized OCI graph')
            identity = descriptor.get('digest')
            require(identity not in visited, 'Duplicate OCI graph descriptor')
            visited.add(identity)
            node = blob(descriptor)
            require(node.get('schemaVersion') == 2, 'Unsupported OCI schema')
            if descriptor.get('mediaType') == INDEX:
                children = node.get('manifests')
                require(isinstance(children, list) and 0 < len(children) <= 1024, 'Invalid OCI index')
                for child in children:
                    walk(child, depth + 1)
                return
            require(descriptor.get('mediaType') == MANIFEST, 'Unsupported OCI image type')
            config = blob(node.get('config'))
            layers = node.get('layers')
            require(isinstance(layers, list), 'Missing OCI layers')
            for layer in layers:
                blob(layer, metadata=False)
            if config.get('os') != 'linux' or config.get('architecture') != 'arm64':
                return
            require(config.get('config', {}).get('User') in {'nonroot', '65532'}, 'Expected official nonroot image')
            labels = config.get('config', {}).get('Labels', {})
            require(labels.get('org.opencontainers.image.revision') == source, 'OCI source label mismatch')
            require(labels.get('org.opencontainers.image.version') == receipt['version'], 'OCI version label mismatch')
            require(node['config']['digest'][7:] == expected.get('config_sha256'), 'OCI config differs from release receipt')
            selected.append(identity)

        root = document('index.json')
        require(root.get('schemaVersion') == 2 and isinstance(root.get('manifests'), list), 'Invalid root OCI index')
        for descriptor in root['manifests']:
            walk(descriptor)
        require(len(selected) == 1, 'Expected exactly one runnable ARM64 image')
    return {'digest': selected[0], 'source': source, 'tag': tag, 'version': receipt['version'],
            'archive_sha256': asset['sha256'], 'manifest_sha256': manifest_sha256}


def memory(value):
    match = re.fullmatch(r'([1-9][0-9]*)(Mi|Gi)', value)
    require(match is not None, 'Memory must be an explicit positive Mi/Gi quantity')
    return int(match[1]) * (1024 if match[2] == 'Gi' else 1)


def render(identity, repository, secret, cpu, request_memory, limit_memory):
    require(re.fullmatch(r'[a-z0-9][a-z0-9.-]*(?::[1-9][0-9]{0,4})?/[a-z0-9]+(?:[._/-][a-z0-9]+)*', repository), 'Expected registry/repository without tag or digest')
    require(len(secret) <= 63 and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', secret), 'Invalid Secret name')
    require(re.fullmatch(r'[1-9][0-9]*m', cpu), 'CPU request must be explicit positive millicores')
    require(memory(limit_memory) >= memory(request_memory), 'Memory limit below request')
    values = {'IMAGE': repository + '@' + identity['digest'], 'SOURCE': identity['source'], 'TAG': identity['tag'],
              'MANIFEST_SHA256': identity['manifest_sha256'], 'SECRET': secret, 'CPU': cpu,
              'REQUEST_MEMORY': request_memory, 'LIMIT_MEMORY': limit_memory}
    template = Path(__file__).with_name('manifest.json.in').read_text()
    for key, value in values.items():
        template = template.replace('@@' + key + '@@', json.dumps(value))
    require('@@' not in template, 'Unresolved manifest placeholder')
    output = read_json(template)
    require(output.get('kind') == 'List' and len(output.get('items', [])) == 4, 'Invalid manifest template')
    return json.dumps(output, indent=2) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('manifest', 'oci', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    for key in ('manifest-sha256', 'source', 'tag', 'repository', 'secret', 'cpu-request', 'memory-request', 'memory-limit'):
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args()
    try:
        identity = verify(args.manifest, args.manifest_sha256, args.oci, args.source, args.tag)
        rendered = render(identity, args.repository, args.secret, args.cpu_request, args.memory_request, args.memory_limit)
        # Exclusive create: failed verification never creates/truncates a manifest.
        with args.output.open('x', encoding='utf-8') as output:
            output.write(rendered)
        print(json.dumps({'status': 'rendered', **identity}, sort_keys=True))
    except (ValueError, OSError, KeyError, TypeError, tarfile.TarError):
        parser.exit(1, 'Relay manifest verification/render failed; no deployment was performed.\n')


if __name__ == '__main__':
    main()
