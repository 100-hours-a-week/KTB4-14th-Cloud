"""Contract tests for scripts.backend_staging_retry.publish.

The module under test is deliberately loaded from the Cloud checkout so these
tests exercise its real candidate ordering and values update calls.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


# Default is portable once this suite is placed under <repo>/tests; CLOUD_REPO
# lets the same file run from /private/tmp during development.
CLOUD = Path(os.environ.get('CLOUD_REPO', Path(__file__).resolve().parents[1]))
SCRIPTS = CLOUD / 'scripts'
for module_name in ('backend_candidate_order', 'backend_staging_values'):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / f'{module_name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    __import__('sys').modules[module_name] = module
spec = importlib.util.spec_from_file_location('retry', SCRIPTS / 'backend_staging_retry.py')
retry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retry)

A, B, C = 'a' * 40, 'b' * 40, 'c' * 40
DIGEST = 'sha256:' + 'd' * 64
OTHER_DIGEST = 'sha256:' + 'e' * 64
BE = '100-hours-a-week/KTB4-14th-BE'


def candidate(sha=B, digest=DIGEST):
    return {
        'schema_version': 1, 'source_repository': BE, 'source_sha': sha,
        'source_ref': 'refs/heads/main', 'image_repository': 'mingkiboo/audigo-backend',
        'image_tag': 'sha-' + sha, 'image_digest': digest, 'platform': 'linux/arm64',
        'workflow_run_id': '123', 'workflow_run_attempt': '2',
        'workflow_run_url': f'https://github.com/{BE}/actions/runs/123/attempts/2',
    }


def compare(base, head, status):
    counts = {'ahead': (1, 0), 'behind': (0, 1), 'identical': (0, 0), 'diverged': (1, 1)}
    ahead, behind = counts[status]
    merge = base if status in ('ahead', 'identical') else head if status == 'behind' else A
    return {'status': status, 'ahead_by': ahead, 'behind_by': behind,
            'base_commit': {'sha': base}, 'merge_base_commit': {'sha': merge}}


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.remote = self.home / 'remote.git'
        self.seed = self.home / 'seed'
        self.dev = self.home / 'developer'
        self.cmd('git', 'init', '--bare', '-q', self.remote)
        self.cmd('git', 'init', '-q', self.seed)
        self.cmd('git', '-C', self.seed, 'config', 'user.name', 'test')
        self.cmd('git', '-C', self.seed, 'config', 'user.email', 'test@example.test')
        for relative in ('apps/spring-value/auth-api-staging.yaml', 'apps/spring-value/chat-ws-staging.yaml'):
            target = self.seed / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CLOUD / relative, target)
        (self.seed / 'unrelated.txt').write_text('base\n')
        self.cmd('git', '-C', self.seed, 'add', '.')
        self.cmd('git', '-C', self.seed, 'commit', '-qm', 'base')
        self.cmd('git', '-C', self.seed, 'branch', '-M', 'main')
        self.cmd('git', '-C', self.seed, 'remote', 'add', 'origin', self.remote)
        self.cmd('git', '-C', self.seed, 'push', '-qu', 'origin', 'main')
        self.cmd('git', 'clone', '-q', self.remote, self.dev)
        self.cmd('git', '-C', self.dev, 'checkout', '-q', 'main')
        self.cmd('git', '-C', self.dev, 'config', 'user.name', 'developer')
        self.cmd('git', '-C', self.dev, 'config', 'user.email', 'developer@example.test')
        (self.dev / 'developer-uncommitted').write_text('must survive\n')
        self.dev_before = {
            'head': self.cmd('git', '-C', self.dev, 'rev-parse', 'HEAD').stdout,
            'status': self.cmd('git', '-C', self.dev, 'status', '--porcelain=v1', '--untracked-files=all').stdout,
            'config': (self.dev / '.git' / 'config').read_bytes(),
        }

    def tearDown(self):
        # publish owns only this prefix, and must remove it on every exit path.
        self.assertFalse(list(self.home.glob('backend-staging-*')))

    def cmd(self, *args, check=True):
        return subprocess.run(args, text=True, capture_output=True, check=check)

    def api(self, main=C):
        def call(path):
            if path.endswith('/commits/main'):
                return {'sha': main}
            tail = path.rsplit('/compare/', 1)[1]
            base, head = tail.split('...')
            if base == head:
                return compare(base, head, 'identical')
            graph = {A: 0, B: 1, C: 2}
            if base not in graph or head not in graph:
                raise retry.RetryError('unexpected BE ref')
            return compare(base, head, 'ahead' if graph[head] > graph[base] else 'behind')
        return call

    def record(self, sha=B, digest=DIGEST):
        retry.values.update(self.seed, candidate(sha, digest))
        p = self.seed / 'apps/spring-value/staging-backend-candidate.json'
        self.assertEqual(json.loads(p.read_text()), candidate(sha, digest))
        self.cmd('git', '-C', self.seed, 'add', '.')
        self.cmd('git', '-C', self.seed, 'commit', '-qm', 'record')
        self.cmd('git', '-C', self.seed, 'push', '-q')
        self.cmd('git', '-C', self.dev, 'fetch', '-q', 'origin', 'main')

    def remote_text(self, relative):
        return self.cmd('git', '--git-dir', self.remote, 'show', f'main:{relative}').stdout

    def unrelated_main_change(self, text='other\n'):
        other = self.home / ('other-' + str(len(list(self.home.glob('other-*')))))
        self.cmd('git', 'clone', '-q', self.remote, other)
        self.cmd('git', '-C', other, 'checkout', '-q', 'main')
        self.cmd('git', '-C', other, 'config', 'user.name', 'other')
        self.cmd('git', '-C', other, 'config', 'user.email', 'other@example.test')
        (other / 'unrelated.txt').write_text(text)
        self.cmd('git', '-C', other, 'add', 'unrelated.txt')
        self.cmd('git', '-C', other, 'commit', '-qm', 'unrelated')
        self.cmd('git', '-C', other, 'push', '-q')

    def assert_dev_unchanged(self):
        self.assertEqual((self.dev / 'developer-uncommitted').read_text(), 'must survive\n')
        self.assertTrue((self.dev / '.git').exists())
        self.assertEqual(self.cmd('git', '-C', self.dev, 'rev-parse', 'HEAD').stdout, self.dev_before['head'])
        self.assertEqual(self.cmd('git', '-C', self.dev, 'status', '--porcelain=v1', '--untracked-files=all').stdout, self.dev_before['status'])
        self.assertEqual((self.dev / '.git' / 'config').read_bytes(), self.dev_before['config'])

    def test_normal_push_and_resource_cleanup(self):
        result = retry.publish(self.dev, candidate(), api=self.api(), temp_parent=self.home)
        self.assertEqual(result, {'status': 'published', 'attempts': 1, 'decision': 'first-registration'})
        record = json.loads(self.remote_text('apps/spring-value/staging-backend-candidate.json'))
        self.assertEqual(record, candidate())
        self.assertIn('tag: sha-' + B, self.remote_text('apps/spring-value/auth-api-staging.yaml'))
        self.assertFalse(list(self.home.glob('backend-staging-attempt-*')))
        self.assert_dev_unchanged()

    def test_conflict_retries_and_preserves_unrelated_main_change(self):
        original = retry.Git.run
        raced = False
        def inject(git, *args, **kwargs):
            nonlocal raced
            if args[:1] == ('push',) and not raced:
                raced = True
                self.unrelated_main_change('preserved\n')
            return original(git, *args, **kwargs)
        with patch.object(retry.Git, 'run', inject):
            result = retry.publish(self.dev, candidate(), api=self.api(), temp_parent=self.home)
        self.assertEqual(result, {'status': 'published', 'attempts': 2, 'decision': 'first-registration'})
        self.assertEqual(self.remote_text('unrelated.txt'), 'preserved\n')
        self.assert_dev_unchanged()

    def test_newer_candidate_concurrently_published_makes_old_stale(self):
        original = retry.Git.run
        done = False
        def inject(git, *args, **kwargs):
            nonlocal done
            if args[:1] == ('push',) and not done:
                done = True
                self.unrelated_main_change('race\n')
                # Publish a valid newer candidate through the same implementation.
                retry.publish(self.dev, candidate(C), api=self.api(), temp_parent=self.home)
            return original(git, *args, **kwargs)
        with patch.object(retry.Git, 'run', inject):
            result = retry.publish(self.dev, candidate(B), api=self.api(), temp_parent=self.home)
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(json.loads(self.remote_text('apps/spring-value/staging-backend-candidate.json'))['source_sha'], C)

    def test_same_candidate_is_unchanged_without_new_commit(self):
        self.record()
        before = self.cmd('git', '--git-dir', self.remote, 'rev-parse', 'main').stdout
        result = retry.publish(self.dev, candidate(), api=self.api(), temp_parent=self.home)
        self.assertEqual(result, {'status': 'unchanged', 'attempts': 1})
        self.assertEqual(before, self.cmd('git', '--git-dir', self.remote, 'rev-parse', 'main').stdout)

    def test_concurrent_same_candidate_is_unchanged_without_duplicate_commit(self):
        original = retry.Git.run
        published = False
        def inject(git, *args, **kwargs):
            nonlocal published
            if args[:1] == ('push',) and not published:
                published = True
                retry.publish(self.dev, candidate(), api=self.api(), temp_parent=self.home)
                return subprocess.CompletedProcess(['git'], 1,
                                                   '!\tHEAD:refs/heads/main\t[rejected] (non-fast-forward)\n', '')
            return original(git, *args, **kwargs)
        with patch.object(retry.Git, 'run', inject):
            result = retry.publish(self.dev, candidate(), api=self.api(), temp_parent=self.home)
        self.assertEqual(result, {'status': 'unchanged', 'attempts': 2})

    def test_same_sha_different_digest_fails(self):
        self.record()
        with self.assertRaises(retry.RetryError):
            retry.publish(self.dev, candidate(B, OTHER_DIGEST), api=self.api(), temp_parent=self.home)
        self.assert_dev_unchanged()

    def test_record_values_mismatch_fails_for_unchanged_and_stale(self):
        self.record()
        value = self.seed / 'apps/spring-value/auth-api-staging.yaml'
        value.write_text(value.read_text().replace('tag: sha-' + B, 'tag: sha-' + A))
        self.cmd('git', '-C', self.seed, 'add', str(value.relative_to(self.seed)))
        self.cmd('git', '-C', self.seed, 'commit', '-qm', 'inconsistent values')
        self.cmd('git', '-C', self.seed, 'push', '-q')
        for new in (candidate(B), candidate(A)):
            with self.subTest(source_sha=new['source_sha']), self.assertRaises(retry.RetryError):
                retry.publish(self.dev, new, api=self.api(), temp_parent=self.home)

    def test_three_non_fast_forward_conflicts_fail(self):
        original = retry.Git.run
        count = 0
        def inject(git, *args, **kwargs):
            nonlocal count
            if args[:1] == ('push',):
                count += 1
                self.unrelated_main_change(f'race-{count}\n')
            return original(git, *args, **kwargs)
        with patch.object(retry.Git, 'run', inject), self.assertRaises(retry.RetryError):
            retry.publish(self.dev, candidate(), api=self.api(), temp_parent=self.home)
        self.assertEqual(count, 3)
        self.assert_dev_unchanged()

    def test_protected_auth_and_unknown_push_fail_without_retry(self):
        for message in ('remote: protected branch hook declined', 'remote: Permission denied', 'fatal: Authentication failed', 'fatal: mysterious push failure'):
            with self.subTest(message=message):
                calls = []
                original = retry.Git.run
                def inject(git, *args, **kwargs):
                    if args[:1] == ('push',):
                        calls.append(args)
                        return subprocess.CompletedProcess(['git'], 1, '', message)
                    return original(git, *args, **kwargs)
                with patch.object(retry.Git, 'run', inject), self.assertRaises(retry.RetryError):
                    retry.publish(self.dev, candidate(), api=self.api(), temp_parent=self.home)
                self.assertEqual(len(calls), 1)

    def test_actual_bare_hook_protection_rejection_stops_immediately(self):
        hook = self.remote / 'hooks' / 'pre-receive'
        hook.write_text('#!/bin/sh\necho "protected branch" >&2\nexit 1\n')
        hook.chmod(0o755)
        with self.assertRaises(retry.RetryError) as failure:
            retry.publish(self.dev, candidate(), api=self.api(), temp_parent=self.home)
        self.assertIn('Push denied', str(failure.exception))

    def test_network_uncertain_push_checks_remote_commit(self):
        original = retry.Git.run
        pushed = False
        def inject(git, *args, **kwargs):
            nonlocal pushed
            result = original(git, *args, **kwargs)
            if args[:1] == ('push',) and not pushed:
                pushed = True
                return subprocess.CompletedProcess(result.args, 1, result.stdout, 'fatal: the remote end hung up unexpectedly')
            return result
        with patch.object(retry.Git, 'run', inject):
            result = retry.publish(self.dev, candidate(), api=self.api(), temp_parent=self.home)
        self.assertEqual(result['status'], 'published')
        # The public contract intentionally stays the same after reconciliation.
        self.assertEqual(result['attempts'], 1)

    def test_unknown_push_with_changed_remote_fails_without_retry(self):
        original = retry.Git.run
        calls = 0
        def inject(git, *args, **kwargs):
            nonlocal calls
            if args[:1] == ('push',):
                calls += 1
                self.unrelated_main_change('advanced-during-unknown\n')
                return subprocess.CompletedProcess(['git'], 1, '', 'fatal: transport unexpectedly closed')
            return original(git, *args, **kwargs)
        with patch.object(retry.Git, 'run', inject), self.assertRaises(retry.RetryError):
            retry.publish(self.dev, candidate(), api=self.api(), temp_parent=self.home)
        self.assertEqual(calls, 1)
        self.assertEqual(self.remote_text('unrelated.txt'), 'advanced-during-unknown\n')

    def test_unreadable_remote_after_uncertain_push_fails(self):
        original = retry.Git.run
        pushed = False
        def inject(git, *args, **kwargs):
            nonlocal pushed
            if args[:1] == ('push',):
                pushed = True
                return subprocess.CompletedProcess(['git'], 1, '', 'fatal: the remote end hung up unexpectedly')
            # Initial scratch fetch succeeds; reconciliation fetch after push fails.
            if pushed and args[:1] == ('fetch',):
                return subprocess.CompletedProcess(['git'], 1, '', 'fatal: network unavailable')
            return original(git, *args, **kwargs)
        with patch.object(retry.Git, 'run', inject), self.assertRaises(retry.RetryError):
            retry.publish(self.dev, candidate(), api=self.api(), temp_parent=self.home)


if __name__ == '__main__':
    unittest.main()
