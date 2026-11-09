"""Receive and publish a verified mockAI staging image candidate."""
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import zipfile

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.tokens import AliasToken, AnchorToken, TagToken
import backend_candidate_order as order
import backend_staging_retry as retry

SOURCE = '100-hours-a-week/KTB4-14th-mockAI'
WORKFLOW = '.github/workflows/cd.yml'
WORKFLOW_NAME = 'AI Image CD'
VALUES = 'apps/ai-values/values-ai-staging.yaml'
RECORD = 'apps/ai-values/staging-ai-candidate.json'
KEYS = {'schema_version', 'source_repository', 'source_sha', 'source_ref', 'image_repository',
        'image_tag', 'image_digest', 'platform', 'workflow_run_id', 'workflow_run_attempt', 'workflow_run_url'}
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')
POSITIVE = re.compile(r'[1-9][0-9]{0,19}')


class Error(ValueError): pass
def require(ok, message):
    if not ok: raise Error(message)
def load_json(text):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, 'Duplicate JSON key'); out[key] = value
        return out
    return json.loads(text, object_pairs_hook=pairs)
def yaml_tool():
    tool = YAML(typ='rt', pure=True); tool.allow_duplicate_keys = False; tool.preserve_quotes = True; tool.width = 4096
    return tool

def repository(root):
    path = Path(root) / VALUES
    require(path.is_file() and not path.is_symlink(), 'AI staging values must be regular')
    text = path.read_text()
    require(not any(isinstance(token, (AliasToken, AnchorToken, TagToken)) for token in yaml_tool().scan(text)),
            'AI staging values cannot use aliases, anchors or explicit tags')
    doc = yaml_tool().load(text)
    require(isinstance(doc, CommentedMap) and isinstance(doc.get('image'), CommentedMap), 'Invalid AI image mapping')
    repo, digest = doc['image'].get('repository'), doc['image'].get('digest')
    require(isinstance(repo, str) and re.fullmatch(r'[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9][a-z0-9._/-]*', repo), 'AI repository must be ECR')
    require(isinstance(digest, str) and DIGEST.fullmatch(digest), 'Invalid AI staging digest')
    return repo

def validate(candidate, repo):
    require(isinstance(candidate, dict) and set(candidate) == KEYS and type(candidate.get('schema_version')) is int and candidate.get('schema_version') == 1, 'Unexpected AI candidate schema')
    require(all(isinstance(candidate[k], str) for k in KEYS - {'schema_version'}), 'Invalid AI candidate fields')
    require(candidate['source_repository'] == SOURCE and candidate['source_ref'] == 'refs/heads/main', 'Candidate repository/ref not allowed')
    require(SHA.fullmatch(candidate['source_sha']) and DIGEST.fullmatch(candidate['image_digest']), 'Invalid candidate SHA/digest')
    require(candidate['image_repository'] == repo and candidate['platform'] == 'linux/arm64', 'Candidate image/platform not allowed')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', candidate['image_tag']), 'Invalid image tag')
    require(all(POSITIVE.fullmatch(candidate[k]) for k in ('workflow_run_id', 'workflow_run_attempt')), 'Invalid run identifiers')
    require(candidate['workflow_run_url'] == f"https://github.com/{SOURCE}/actions/runs/{candidate['workflow_run_id']}/attempts/{candidate['workflow_run_attempt']}", 'Invalid candidate run URL')
    return candidate

def candidate_zip(data, repo):
    require(isinstance(data, bytes) and len(data) <= 1024 * 1024, 'Candidate artifact too large')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            require(len(entries) == 1 and entries[0].filename == 'image-candidate.json' and not entries[0].is_dir() and not entries[0].flag_bits & 1 and entries[0].file_size <= 65536, 'Invalid candidate artifact')
            return validate(load_json(archive.read(entries[0]).decode()), repo)
    except (zipfile.BadZipFile, UnicodeError) as exc: raise Error('Invalid candidate artifact') from exc

class Client:
    def api(self, path):
        token = os.environ.get('AI_READ_TOKEN'); require(bool(token), 'AI_READ_TOKEN is required')
        result = subprocess.run(['gh', 'api', f'repos/{SOURCE}/{path}'], env=dict(os.environ, GH_TOKEN=token), capture_output=True, text=True)
        require(result.returncode == 0, 'AI GitHub API lookup failed')
        try: return load_json(result.stdout)
        except ValueError as exc: raise Error('AI GitHub API returned invalid JSON') from exc
    def download(self, artifact_id):
        token = os.environ.get('AI_READ_TOKEN'); require(bool(token), 'AI_READ_TOKEN is required')
        request = urllib.request.Request(f'https://api.github.com/repos/{SOURCE}/actions/artifacts/{artifact_id}/zip', headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'})
        try: response = urllib.request.build_opener(NoRedirect()).open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            with exc: require(exc.code == 302, 'AI candidate artifact download failed'); location = exc.headers.get('Location', '')
            parts = urlsplit(location); require(parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password, 'Unsafe artifact redirect')
            response = urllib.request.build_opener(HTTPSRedirect()).open(urllib.request.Request(location), timeout=30)
        with response: return response.read(1024 * 1024 + 1)
    def ecr(self, *args):
        result = subprocess.run(['aws', 'ecr', *args, '--output', 'json'], capture_output=True, text=True)
        require(result.returncode == 0, 'ECR image validation failed')
        try: return load_json(result.stdout)
        except ValueError as exc: raise Error('ECR returned invalid JSON') from exc
    def registry(self, reference, field):
        result = subprocess.run(['docker', 'buildx', 'imagetools', 'inspect', reference, '--format', '{{json .' + field + '}}'], capture_output=True, text=True)
        require(result.returncode == 0 and len(result.stdout) <= 1024 * 1024, 'Registry lookup failed')
        try: return load_json(result.stdout)
        except ValueError as exc: raise Error('Invalid registry response') from exc
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None
class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl); require(parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password, 'Unsafe artifact redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def fetch_candidate(root, run_id, attempt, client):
    require(POSITIVE.fullmatch(run_id or '') and POSITIVE.fullmatch(attempt or ''), 'Invalid dispatch identifiers')
    repo = repository(root); workflow, run = client.api('actions/workflows/cd.yml'), client.api(f'actions/runs/{run_id}/attempts/{attempt}')
    require(workflow.get('path') == WORKFLOW and isinstance(workflow.get('id'), int) and run.get('workflow_id') == workflow['id'] and run.get('path') == WORKFLOW and run.get('name') == WORKFLOW_NAME and str(run.get('id')) == run_id and str(run.get('run_attempt')) == attempt and run.get('event') == 'push' and run.get('head_branch') == 'main' and run.get('status') == 'completed' and run.get('conclusion') == 'success' and run.get('repository', {}).get('full_name') == SOURCE and run.get('head_repository', {}).get('full_name') == SOURCE, 'Run is not the designated successful main AI publish attempt')
    sha = run.get('head_sha'); require(isinstance(sha, str) and SHA.fullmatch(sha), 'Invalid source SHA')
    response = client.api(f'actions/runs/{run_id}/artifacts?per_page=100&page=1')
    require(isinstance(response.get('artifacts'), list) and response.get('total_count') == len(response['artifacts']), 'Incomplete artifact list')
    name = f'ai-image-candidate-{sha}-{run_id}-{attempt}'
    found = [item for item in response['artifacts'] if isinstance(item, dict) and item.get('name') == name]
    require(len(found) == 1 and found[0].get('expired') is False and isinstance(found[0].get('id'), int), 'AI candidate artifact missing, duplicated or expired')
    candidate = candidate_zip(client.download(found[0]['id']), repo)
    require(candidate['source_sha'] == sha and candidate['workflow_run_id'] == run_id and candidate['workflow_run_attempt'] == attempt, 'Candidate does not match AI run')
    return candidate

def validate_ecr(client, candidate):
    name = candidate['image_repository'].split('/', 1)[1]
    details = client.ecr('describe-images', '--repository-name', name, '--image-ids', f"imageDigest={candidate['image_digest']}").get('imageDetails')
    require(isinstance(details, list) and len(details) == 1 and candidate['image_tag'] in details[0].get('imageTags', []), 'ECR digest and candidate tag do not identify the same image')
    manifest = client.registry(candidate['image_repository'] + '@' + candidate['image_digest'], 'Manifest')
    require(isinstance(manifest, dict) and manifest.get('schemaVersion') == 2 and manifest.get('digest') == candidate['image_digest'], 'Registry manifest digest mismatch')
    kind = manifest.get('mediaType')
    if kind in ('application/vnd.oci.image.index.v1+json', 'application/vnd.docker.distribution.manifest.list.v2+json'):
        arm = [item for item in manifest.get('manifests', []) if isinstance(item, dict) and item.get('platform', {}).get('os') == 'linux' and item.get('platform', {}).get('architecture') == 'arm64']
        require(bool(arm), 'Registry image is missing linux/arm64 manifest')
        image = client.registry(candidate['image_repository'] + '@' + arm[0].get('digest', ''), 'Image')
    else:
        require(kind in ('application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.v2+json'), 'Unsupported registry manifest')
        image = client.registry(candidate['image_repository'] + '@' + candidate['image_digest'], 'Image')
    require(isinstance(image, dict) and image.get('os') == 'linux' and image.get('architecture') == 'arm64', 'Registry image is not linux/arm64')

def git(root, *args, check=True):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
    if check and result.returncode: raise Error('Git operation failed: ' + args[0])
    return result
def current(tree):
    path = Path(tree) / RECORD
    if not path.exists() and not path.is_symlink(): return None
    require(path.is_file() and not path.is_symlink(), 'AI candidate record must be a regular file')
    candidate = validate(load_json(path.read_text()), repository(tree))
    doc = yaml_tool().load((Path(tree) / VALUES).read_text())
    require(doc['image'].get('digest') == candidate['image_digest'], 'AI values and candidate record differ')
    return candidate
def decision(candidate, old, client):
    # Reuse the BE comparison contract without changing its repository global.
    def api(path):
        prefix = 'repos/' + order.REPOSITORY + '/'
        require(path.startswith(prefix), 'Unexpected shared ordering API path')
        return client.api(path[len(prefix):])
    main = api(f'repos/{order.REPOSITORY}/commits/main').get('sha'); require(isinstance(main, str) and SHA.fullmatch(main), 'Invalid AI main SHA')
    require(order.compare(api, candidate['source_sha'], main) in ('ahead', 'identical'), 'Candidate is not in AI main history')
    if old is None: return 'first-registration'
    if old['source_sha'] == candidate['source_sha']:
        require(old['image_digest'] == candidate['image_digest'], 'Same source SHA has a different digest'); return 'unchanged'
    status = order.compare(api, old['source_sha'], candidate['source_sha'])
    if status == 'ahead': return 'update'
    if status == 'behind': return 'stale'
    raise Error('AI staging and candidate histories diverge or cannot be ordered')
def update(tree, candidate):
    root = Path(tree); require(not git(root, 'status', '--porcelain').stdout.strip(), 'AI staging updater requires clean checkout')
    validate(candidate, repository(root))
    path = root / VALUES; original = path.read_text()
    # Keep formatting/comments byte-identical except the one image.digest scalar.
    blocks = re.findall(r'(?ms)^image:\n(?:^[ \t]+.*\n?)*', original)
    require(len(blocks) == 1, 'Expected one image mapping')
    updated_block, count = re.subn(r'(?m)^(\s*digest:\s*)(["\']?)sha256:[0-9a-f]{64}\2\s*$', r'\g<1>"' + candidate['image_digest'] + '"', blocks[0])
    require(count == 1, 'Expected one image.digest')
    path.write_text(original.replace(blocks[0], updated_block, 1))
    record = root / RECORD; require(not record.is_symlink(), 'AI candidate record cannot be a symlink')
    record.write_text(json.dumps(candidate, indent=2) + '\n')
    require(set(git(root, 'status', '--porcelain').stdout.splitlines()) <= {f' M {VALUES}', f'?? {RECORD}', f' M {RECORD}'}, 'AI update changed unexpected files')
def publish(root, candidate, client, temp_parent=None):
    remote = git(root, 'remote', 'get-url', 'origin').stdout.strip(); parent = Path(temp_parent or tempfile.gettempdir())
    with tempfile.TemporaryDirectory(prefix='ai-staging-', dir=parent) as tmp:
        bare = Path(tmp) / 'repo.git'; bare.mkdir(); git(bare, 'init', '--bare', '-q'); git(bare, 'remote', 'add', 'origin', remote)
        for attempt in range(1, 4):
            git(bare, 'fetch', '--no-tags', 'origin', 'refs/heads/main'); revision = git(bare, 'rev-parse', 'FETCH_HEAD').stdout.strip(); tree = Path(tmp) / f'attempt-{attempt}'; git(bare, 'worktree', 'add', '--detach', str(tree), revision)
            try:
                state = decision(candidate, current(tree), client)
                if state in ('unchanged', 'stale'): return {'status': state, 'attempts': attempt}
                update(tree, candidate); git(tree, 'add', '--', VALUES, RECORD); git(tree, 'diff', '--cached', '--check'); git(tree, '-c', 'user.name=github-actions[bot]', '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com', 'commit', '-qm', 'chore(staging): update AI image candidate')
                commit = git(tree, 'rev-parse', 'HEAD').stdout.strip(); pushed = git(tree, 'push', '--porcelain', 'origin', 'HEAD:refs/heads/main', check=False)
                if pushed.returncode == 0: return {'status': 'published', 'attempts': attempt, 'decision': state, 'cloud_commit': commit}
                kind = retry.classify_push(pushed)
                if kind == 'unknown':
                    if retry.confirmed(retry.Git(bare), commit): return {'status': 'published', 'attempts': attempt, 'decision': state, 'cloud_commit': commit}
                    raise Error('AI staging push outcome is uncertain')
                if kind != 'conflict': raise Error('AI staging push denied')
            finally: git(bare, 'worktree', 'remove', '--force', str(tree))
        raise Error('AI staging non-fast-forward retry limit reached')
def execute(root, run_id, attempt, client, temp_parent=None):
    candidate = fetch_candidate(root, run_id, attempt, client); validate_ecr(client, candidate); result = publish(root, candidate, client, temp_parent)
    return dict(result, candidate=candidate, source_sha=candidate['source_sha'], image_digest=candidate['image_digest'], workflow_run_id=run_id, workflow_run_attempt=attempt, workflow_run_url=candidate['workflow_run_url'])
def main():
    try:
        require(os.environ.get('GITHUB_ACTIONS') == 'true', 'AI staging entry point requires GitHub Actions'); print(json.dumps(execute(Path.cwd(), os.environ.get('RUN_ID', ''), os.environ.get('RUN_ATTEMPT', ''), Client()))); return 0
    except Exception as exc: print(str(exc) if isinstance(exc, Error) else 'AI candidate validation failed', file=sys.stderr); return 1
if __name__ == '__main__': sys.exit(main())
