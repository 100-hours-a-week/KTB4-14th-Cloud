"""Validate an immutable BE candidate before invoking the bounded staging publisher."""
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import zipfile

import backend_candidate_order as order
import backend_staging_retry as retry
import backend_staging_values as values

BE = '100-hours-a-week/KTB4-14th-BE'
PREFIX = f'repos/{BE}'
MAX_ZIP = 1024 * 1024
MAX_JSON = 64 * 1024


class IntegrationError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise IntegrationError(message)


def positive(value):
    return isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,19}', value) is not None


def candidate_from_zip(data):
    require(isinstance(data, bytes) and len(data) <= MAX_ZIP, 'Artifact exceeds archive size limit')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            require(len(entries) == 1, 'Artifact must contain only candidate JSON')
            entry = entries[0]
            mode = entry.external_attr >> 16
            require(entry.filename == 'image-candidate.json' and not entry.is_dir()
                    and not entry.flag_bits & 1
                    and stat.S_IFMT(mode) in (0, stat.S_IFREG)
                    and entry.file_size <= MAX_JSON, 'Unsafe candidate archive entry')
            with archive.open(entry) as stream:
                raw = stream.read(MAX_JSON + 1)
            require(len(raw) <= MAX_JSON, 'Candidate JSON exceeds size limit')
        return values.validate_candidate(values.load_json(raw.decode('utf-8')))
    except (zipfile.BadZipFile, UnicodeError, RuntimeError) as exc:
        raise IntegrationError('Invalid candidate archive') from exc


def validate_registry(client, candidate):
    repo = candidate['image_repository']
    digest = candidate['image_digest']
    manifest = client.manifest(f'{repo}@{digest}')
    require(isinstance(manifest, dict) and manifest.get('schemaVersion') == 2
            and manifest.get('digest') == digest, 'Registry manifest digest mismatch or missing')
    kind = manifest.get('mediaType')
    if kind in ('application/vnd.oci.image.index.v1+json',
                'application/vnd.docker.distribution.manifest.list.v2+json'):
        descriptors = manifest.get('manifests')
        require(isinstance(descriptors, list), 'Invalid image index')
        matching = [d for d in descriptors if isinstance(d, dict)
                    and d.get('platform', {}).get('os') == 'linux'
                    and d.get('platform', {}).get('architecture') == 'arm64']
        require(bool(matching), 'Image index does not include linux/arm64')
        for descriptor in matching:
            child = descriptor.get('digest')
            require(isinstance(child, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', child),
                    'Invalid ARM64 image descriptor')
            image = client.image(f'{repo}@{child}')
            require(isinstance(image, dict) and image.get('os') == 'linux'
                    and image.get('architecture') == 'arm64', 'ARM64 image config mismatch')
    else:
        require(kind in ('application/vnd.oci.image.manifest.v1+json',
                         'application/vnd.docker.distribution.manifest.v2+json'),
                'Unsupported image manifest type')
        image = client.image(f'{repo}@{digest}')
        require(isinstance(image, dict) and image.get('os') == 'linux'
                and image.get('architecture') == 'arm64', 'Image is not linux/arm64')


def execute(repo, run_id, attempt, client, temp_parent=None, *, validate_only=False):
    require(positive(run_id) and positive(attempt), 'Invalid dispatch identifiers')
    workflow = client.api(f'{PREFIX}/actions/workflows/publish-image.yml')
    run = client.api(f'{PREFIX}/actions/runs/{run_id}/attempts/{attempt}')
    require(isinstance(workflow, dict) and isinstance(run, dict), 'Invalid workflow API response')
    require(type(workflow.get('id')) is int and workflow['id'] > 0
            and workflow.get('path') == '.github/workflows/publish-image.yml'
            and run.get('workflow_id') == workflow['id']
            and run.get('path') == workflow['path']
            and run.get('name') == 'Publish backend image'
            and type(run.get('id')) is int and str(run['id']) == run_id
            and type(run.get('run_attempt')) is int and str(run['run_attempt']) == attempt
            and run.get('event') == 'push' and run.get('head_branch') == 'main'
            and run.get('status') == 'completed' and run.get('conclusion') == 'success'
            and run.get('repository', {}).get('full_name') == BE
            and run.get('head_repository', {}).get('full_name') == BE,
            'Run is not the designated successful main publish attempt')
    sha = run.get('head_sha')
    require(isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{40}', sha), 'Invalid source SHA')
    name = f'image-candidate-{sha}-{run_id}-{attempt}'
    artifacts = []
    total = None
    for page in range(1, 102):
        response = client.api(f'{PREFIX}/actions/runs/{run_id}/artifacts?per_page=100&page={page}')
        require(isinstance(response, dict) and type(response.get('total_count')) is int
                and 0 <= response['total_count'] <= 10000
                and isinstance(response.get('artifacts'), list), 'Invalid artifact list')
        if total is None:
            total = response['total_count']
        require(total == response['total_count'], 'Artifact list changed during pagination')
        entries = response['artifacts']
        require(all(isinstance(a, dict) and type(a.get('id')) is int and a['id'] > 0
                    for a in entries), 'Invalid artifact metadata')
        artifacts.extend(entries)
        require(len(artifacts) <= total and len({a['id'] for a in artifacts}) == len(artifacts),
                'Inconsistent artifact pagination')
        if len(artifacts) == total:
            break
        require(bool(entries), 'Incomplete artifact pagination')
    else:
        raise IntegrationError('Artifact pagination limit exceeded')
    found = [a for a in artifacts if a.get('name') == name]
    require(len(found) == 1 and found[0].get('expired') is False,
            'Candidate artifact missing, duplicated or expired')
    candidate = candidate_from_zip(client.download(found[0]['id']))
    require(candidate['source_sha'] == sha and candidate['workflow_run_id'] == run_id
            and candidate['workflow_run_attempt'] == attempt, 'Candidate does not match API run')
    validate_registry(client, candidate)
    if validate_only:
        return candidate
    result = retry.publish(Path(repo), candidate, api=client.api, temp_parent=temp_parent)
    return dict(result, source_sha=sha, image_digest=candidate['image_digest'],
                image_repository=candidate['image_repository'], workflow_run_url=candidate['workflow_run_url'])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        require(parts.scheme == 'https' and parts.hostname and not parts.username
                and not parts.password, 'Unsafe artifact redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    api = staticmethod(order.gh_api)

    def download(self, artifact_id):
        token = os.environ.get('GH_TOKEN')
        require(bool(token), 'BE read authentication is missing')
        request = urllib.request.Request(
            f'https://api.github.com/{PREFIX}/actions/artifacts/{artifact_id}/zip',
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28'})
        try:
            response = urllib.request.build_opener(NoRedirect()).open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            with exc:
                require(exc.code == 302, 'Artifact download API failed')
                location = exc.headers.get('Location', '')
            parts = urlsplit(location)
            require(parts.scheme == 'https' and parts.hostname and not parts.username
                    and not parts.password, 'Unsafe artifact download location')
            # A fresh request intentionally carries no Authorization, even across further redirects.
            response = urllib.request.build_opener(HTTPSRedirect()).open(
                urllib.request.Request(location), timeout=30)
        with response:
            data = response.read(MAX_ZIP + 1)
        require(len(data) <= MAX_ZIP, 'Artifact exceeds archive size limit')
        return data

    def inspect(self, reference, field):
        process = subprocess.run(['docker', 'buildx', 'imagetools', 'inspect', reference,
                                  '--format', '{{json .' + field + '}}'], capture_output=True, text=True)
        require(process.returncode == 0 and len(process.stdout) <= MAX_ZIP,
                'Registry lookup failed')
        try:
            return json.loads(process.stdout)
        except ValueError as exc:
            raise IntegrationError('Invalid registry response') from exc

    def manifest(self, reference):
        return self.inspect(reference, 'Manifest')

    def image(self, reference):
        return self.inspect(reference, 'Image')


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        print('Staging entry point requires GitHub Actions', file=sys.stderr)
        return 1
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    try:
        result = execute(Path.cwd(), os.environ.get('RUN_ID', ''),
                         os.environ.get('RUN_ATTEMPT', ''), Client())
        text = (f"### Staging candidate: {result['status']}\n"
                f"- Source SHA: `{result['source_sha']}`\n"
                f"- Image: `{result['image_repository']}@{result['image_digest']}`\n"
                f"- Order decision: `{result.get('decision', result['status'])}` (staging tracking only; not a production baseline)\n"
                f"- Push attempts: {result['attempts']}\n"
                f"- [BE publish run]({result['workflow_run_url']})\n"
                'This is a traceability record, not cryptographic provenance or staging verification.\n')
        print(json.dumps(result))
        status = 0
    except Exception as exc:
        # Never print API/registry/Git diagnostics: they can contain signed URLs or credentials.
        reason = str(exc) if isinstance(exc, IntegrationError) else (
            'Candidate ordering or publication failed' if isinstance(exc, (order.OrderError, retry.RetryError))
            else 'API, archive, schema or registry validation failed')
        text = f'### Staging candidate: failed\n{reason}. Success is not confirmed.\n'
        print(reason, file=sys.stderr)
        status = 1
    if summary:
        with open(summary, 'a', encoding='utf-8') as stream:
            stream.write(text)
    return status


if __name__ == '__main__':
    sys.exit(main())
