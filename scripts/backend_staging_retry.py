"""Publish a staging candidate from isolated worktrees; retry only explicit NFF."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import backend_candidate_order as order
import backend_staging_values as values


class RetryError(ValueError):
    pass


class Git:
    def __init__(self, root):
        self.root = Path(root)

    def run(self, *args, check=True):
        env = dict(os.environ, LC_ALL='C', GIT_TERMINAL_PROMPT='0')
        result = subprocess.run(['git', '-c', 'credential.helper=', '-C', str(self.root), *args],
                                capture_output=True, text=True, env=env)
        if check and result.returncode:
            # Git diagnostics may contain signed URLs or credentials; never echo them.
            raise RetryError('Git operation failed: ' + args[0])
        return result


def classify_push(result):
    text = result.stdout + '\n' + result.stderr
    lower = text.lower()
    if any(x in lower for x in ('[remote rejected]', 'authentication failed', 'permission denied',
                               'could not read username', 'http 403', 'error: 403', 'returned error: 403', 'returned error: 401', 'repository not found',
                               'protected branch', 'repository rule', 'gh006', 'gh013', 'access denied')):
        return 'denied'
    # Match the porcelain destination and reason, not a moving remote HEAD.
    lines = result.stdout.splitlines()
    if any(line.startswith('!\t') and line.split('\t')[-1] in (
            '[rejected] (fetch first)', '[rejected] (non-fast-forward)') for line in lines):
        return 'conflict'
    return 'unknown'


def read_current(tree):
    path = tree / values.RECORD
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise RetryError('Staging record must be a regular file')
    current = values.validate_candidate(values.load_json(path.read_text()))
    # Even stale/unchanged decisions require values to agree with the recorded candidate.
    values.verify(tree, current)
    return current


def confirmed(git, commit):
    # Resolve uncertain delivery by reading the actual remote branch. Failure is fatal.
    git.run('fetch', '--no-tags', 'origin', 'refs/heads/main')
    remote = git.run('rev-parse', 'FETCH_HEAD').stdout.strip()
    answer = git.run('merge-base', '--is-ancestor', commit, remote, check=False)
    if answer.returncode == 0:
        return True
    if answer.returncode == 1:
        return False
    raise RetryError('Cannot determine whether push reached remote main')


def _publish(repo, candidate, api, temp_parent):
    values.validate_candidate(candidate)
    repo = Path(repo).resolve()
    remote = Git(repo).run('remote', 'get-url', 'origin').stdout.strip()
    # Do not copy token-bearing URLs into even temporary Git configuration.
    if not remote or '\n' in remote or ('://' in remote and '@' in remote.split('://', 1)[1].split('/', 1)[0]):
        raise RetryError('Origin URL must not contain credentials')
    parent = Path(temp_parent or tempfile.gettempdir()).resolve()
    if parent == repo or repo in parent.parents:
        raise RetryError('Temporary resources must be outside the source worktree')
    with tempfile.TemporaryDirectory(prefix='backend-staging-', dir=parent) as tmp:
        scratch = Path(tmp) / 'repo.git'
        scratch.mkdir()
        git = Git(scratch)
        git.run('init', '--bare', '-q')
        git.run('remote', 'add', 'origin', remote)
        for attempt in range(1, 4):
            git.run('fetch', '--no-tags', 'origin', 'refs/heads/main')
            revision = git.run('rev-parse', 'FETCH_HEAD').stdout.strip()
            tree = Path(tmp) / f'attempt-{attempt}'
            git.run('worktree', 'add', '--detach', str(tree), revision)
            try:
                current = read_current(tree)
                decision = order.decide(candidate, current, api)
                if decision in ('unchanged', 'stale'):
                    return {'status': decision, 'attempts': attempt}
                if decision not in ('first-registration', 'update'):
                    raise RetryError('Unexpected candidate order decision')
                values.update(tree, candidate)
                values.verify(tree, candidate)
                work = Git(tree)
                work.run('add', '--', *values.VALUES, values.RECORD)
                values.verify(tree, candidate)
                diff = work.run('diff', '--cached', '--quiet', check=False)
                if diff.returncode == 0:
                    return {'status': 'unchanged', 'attempts': attempt}
                if diff.returncode != 1:
                    raise RetryError('Cannot inspect staged changes')
                work.run('-c', 'user.name=github-actions[bot]', '-c',
                         'user.email=41898282+github-actions[bot]@users.noreply.github.com',
                         'commit', '-qm', 'chore(staging): update backend image candidate')
                commit = work.run('rev-parse', 'HEAD').stdout.strip()
                pushed = work.run('push', '--porcelain', 'origin', 'HEAD:refs/heads/main', check=False)
                if pushed.returncode == 0:
                    return {'status': 'published', 'attempts': attempt, 'decision': decision}
                kind = classify_push(pushed)
                if kind == 'denied':
                    raise RetryError('Push denied: authentication, permissions, server hook or branch protection')
                if kind == 'unknown':
                    if confirmed(git, commit):
                        return {'status': 'published', 'attempts': attempt, 'decision': decision}
                    raise RetryError('Push failed with unknown cause; remote does not confirm this commit')
                # Explicit NFF only: next iteration fetches/rejudges/regenerates everything.
            finally:
                git.run('worktree', 'remove', '--force', str(tree))
        raise RetryError('Non-fast-forward retry limit reached (3 attempts)')


def publish(repo, candidate, api=order.gh_api, temp_parent=None):
    try:
        return _publish(repo, candidate, api, temp_parent)
    except (order.OrderError, values.ValidationError, values.YAMLError, OSError) as exc:
        raise RetryError(str(exc)) from exc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--repo', default='.')
    args = parser.parse_args()
    # CLI writes are authorized only by the actual workflow context, never local work.
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.error('Publishing CLI is restricted to GitHub Actions')
    try:
        candidate = values.load_json(Path(args.candidate).read_text())
        print(json.dumps(publish(Path(args.repo), candidate)))
    except (ValueError, OSError, values.YAMLError) as exc:
        print(f'Staging publication failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
