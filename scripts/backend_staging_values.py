"""Transactional staging values update, with Git and parsed-field allowlists."""
import argparse
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from ruamel.yaml.error import YAMLError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.tokens import AliasToken, AnchorToken, TagToken

VALUES = ('apps/spring-value/auth-api-staging.yaml', 'apps/spring-value/chat-ws-staging.yaml')
RECORD = 'apps/spring-value/staging-backend-candidate.json'
ALLOWED = {*VALUES, RECORD}
IMAGE = 'mingkiboo/audigo-backend'
BE = '100-hours-a-week/KTB4-14th-BE'
KEYS = {'schema_version', 'source_repository', 'source_sha', 'source_ref', 'image_repository',
        'image_tag', 'image_digest', 'platform', 'workflow_run_id', 'workflow_run_attempt', 'workflow_run_url'}


class ValidationError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise ValidationError(message)


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def load_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs)


def validate_candidate(c):
    require(isinstance(c, dict) and set(c) == KEYS, 'Unexpected candidate schema')
    require(type(c['schema_version']) is int and c['schema_version'] == 1, 'Invalid schema version')
    require(all(isinstance(c[k], str) for k in KEYS - {'schema_version'}), 'Invalid candidate field type')
    require(re.fullmatch('[0-9a-f]{40}', c['source_sha']), 'Invalid source SHA')
    require(re.fullmatch('sha256:[0-9a-f]{64}', c['image_digest']), 'Invalid digest')
    require(c['image_repository'] == IMAGE and c['source_repository'] == BE, 'Repository not allowed')
    require(c['source_ref'] == 'refs/heads/main' and c['platform'] == 'linux/arm64', 'Invalid ref/platform')
    require(c['image_tag'] == 'sha-' + c['source_sha'], 'Invalid tag')
    require(all(re.fullmatch('[1-9][0-9]*', c[k]) for k in ('workflow_run_id', 'workflow_run_attempt')), 'Invalid run/attempt')
    url = f"https://github.com/{BE}/actions/runs/{c['workflow_run_id']}/attempts/{c['workflow_run_attempt']}"
    require(c['workflow_run_url'] == url, 'Invalid source URL')
    return c


def yaml_tool():
    y = YAML(typ='rt', pure=True)
    y.allow_duplicate_keys = False
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096
    return y


def parse_yaml(text):
    # Aliases/merges/custom tags can conceal shared objects and unexpected types.
    require(not any(isinstance(t, (AliasToken, AnchorToken, TagToken)) for t in yaml_tool().scan(text)),
            'YAML anchors, aliases and explicit tags are not supported')
    doc = yaml_tool().load(text)
    def plain(v):
        if isinstance(v, dict):
            require(all(isinstance(k, str) for k in v), 'Non-string YAML key')
            require('<<' not in v, 'YAML merge is not supported')
            return {str(k): plain(x) for k, x in v.items()}
        if isinstance(v, list):
            return [plain(x) for x in v]
        require(v is None or isinstance(v, (str, bool, int, float)), 'Unexpected YAML value type')
        # Keep scalar types distinguishable during comparison (True != 1).
        kind = 'null' if v is None else 'str' if isinstance(v, str) else 'bool' if isinstance(v, bool) else 'int' if isinstance(v, int) else 'float'
        return (kind, str(v))
    require(isinstance(doc, CommentedMap) and isinstance(doc.get('image'), CommentedMap), 'Expected image mapping')
    require(doc['image'].get('repository') == IMAGE, 'Values repository not allowed')
    require(isinstance(doc['image'].get('digest'), str) and re.fullmatch('sha256:[0-9a-f]{64}', doc['image']['digest']), 'Invalid values digest')
    require('tag' not in doc['image'] or isinstance(doc['image']['tag'], str), 'Invalid values tag')
    plain(doc)
    return doc, plain


def compare_values(before, after, c):
    a, normalize = parse_yaml(before)
    b, _ = parse_yaml(after)
    require(b['image'].get('tag') == c['image_tag'] and b['image']['digest'] == c['image_digest'], 'Values do not match candidate')
    for doc in (a, b):
        doc['image'].pop('tag', None)
        doc['image'].pop('digest', None)
    require(normalize(a) == normalize(b), 'Non-allowlisted YAML field changed')


def baseline(root, name):
    mode = git(root, 'ls-tree', 'HEAD', '--', name).decode().split(' ', 1)[0]
    require(mode in ('100644', '100755'), 'Values must be tracked regular files')
    return git(root, 'show', f'HEAD:{name}').decode()


def verify(root, c):
    validate_candidate(c)
    # --no-renames reports renames as delete/add, so either side is rejected.
    statuses = git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignored=matching', '--no-renames').decode().split('\0')
    for entry in filter(None, statuses):
        status, name = entry[:2], entry[3:]
        require(name in ALLOWED, 'Non-allowlisted file changed: ' + name)
        require(status in ((' M', 'M ', 'MM') if name in VALUES else (' M', 'M ', 'MM', '??', 'A ')), 'File deletion, rename or replacement not allowed')
    for name in VALUES:
        p = root / name
        require(p.is_file() and not p.is_symlink(), 'Values must remain regular files')
        before = baseline(root, name)
        expected_mode = git(root, 'ls-tree', 'HEAD', '--', name).decode().split(' ', 1)[0]
        require(bool(p.stat().st_mode & 0o111) == (expected_mode == '100755'), 'Values mode changed')
        compare_values(before, p.read_text(), c)
        # Validate staged content too: a later commit uses the index, not the worktree.
        if git(root, 'diff', '--cached', '--name-only', '--', name).strip():
            compare_values(before, git(root, 'show', f':{name}').decode(), c)
    p = root / RECORD
    require(p.is_file() and not p.is_symlink(), 'Candidate record must be a regular file')
    require(validate_candidate(load_json(p.read_text())) == c, 'Candidate record mismatch')
    if git(root, 'diff', '--cached', '--name-only', '--', RECORD).strip():
        require(validate_candidate(load_json(git(root, 'show', f':{RECORD}').decode())) == c, 'Staged record mismatch')
    git(root, 'diff', '--check')
    git(root, 'diff', '--cached', '--check')


def update(root, c):
    root = Path(root).resolve()
    validate_candidate(c)
    require(not git(root, 'status', '--porcelain=v1', '--untracked-files=all', '--ignored=matching').strip(), 'Updater requires a clean checkout')
    original, outputs = {}, {}
    for name in VALUES:
        p = root / name
        require(p.is_file() and not p.is_symlink(), 'Values must exist as regular files')
        text = p.read_text()
        require(text == baseline(root, name), 'Values differ from HEAD')
        doc, _ = parse_yaml(text)
        doc['image']['tag'] = c['image_tag']
        doc['image']['digest'] = c['image_digest']
        buf = io.StringIO(); yaml_tool().dump(doc, buf)
        compare_values(text, buf.getvalue(), c)
        outputs[name] = buf.getvalue().encode()
    p = root / RECORD
    require(not p.is_symlink(), 'Symlink record not allowed')
    if p.exists():
        validate_candidate(load_json(p.read_text()))
    outputs[RECORD] = (json.dumps(c, indent=2, ensure_ascii=False) + '\n').encode()
    for name in outputs:
        p = root / name
        original[name] = p.read_bytes() if p.exists() else None
    staged = {}
    try:
        # Prepare all files before replacing any; each replacement is atomic.
        for name, data in outputs.items():
            p = root / name
            fd, tmp = tempfile.mkstemp(prefix='.candidate-', dir=p.parent)
            staged[name] = tmp
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
            os.chmod(tmp, p.stat().st_mode & 0o777 if p.exists() else 0o644)
        for name, tmp in staged.items():
            os.replace(tmp, root / name)
        verify(root, c)
    except BaseException:
        for name, data in original.items():
            p = root / name
            if data is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(data)
        raise
    finally:
        for tmp in staged.values():
            Path(tmp).unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=('update', 'verify'))
    p.add_argument('--root', default='.')
    p.add_argument('--candidate', required=True)
    args = p.parse_args()
    try:
        c = validate_candidate(load_json(Path(args.candidate).read_text()))
        (update if args.command == 'update' else verify)(Path(args.root).resolve(), c)
    except (ValueError, OSError, YAMLError, subprocess.CalledProcessError) as exc:
        print(f'Staging values validation failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
