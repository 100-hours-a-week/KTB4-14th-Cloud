"""Exercise actual promotion implementation against full local Git histories; no services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import backend_production as production
import backend_staging_values as values

D1, D2 = 'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE).decode().strip()


def init(root):
    root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.email', 'test@example.invalid')
    git(root, 'config', 'user.name', 'Local fixture')


def commit(root):
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'fixture')
    return git(root, 'rev-parse', 'HEAD')


class Promotion(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.be, self.cloud = self.base / 'be', self.base / 'cloud'
        init(self.be); init(self.cloud)
        (self.be / 'README.md').write_text('base')
        self.old = commit(self.be)

    def tearDown(self):
        self.temp.cleanup()

    def change(self, path, text='changed'):
        p = self.be / path; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
        return commit(self.be)

    def setup_cloud(self, new):
        c = dict(schema_version=1, source_repository=values.BE, source_sha=new,
                 source_ref='refs/heads/main', image_repository=values.IMAGE,
                 image_tag='sha-' + new, image_digest=D2, platform='linux/arm64',
                 workflow_run_id='123', workflow_run_attempt='1',
                 workflow_run_url=f'https://github.com/{values.BE}/actions/runs/123/attempts/1')
        for t in production.TARGETS:
            name = f'apps/spring-value/{t}-prod.yaml'
            p = self.cloud / name; p.parent.mkdir(parents=True, exist_ok=True)
            doc, _ = values.parse_yaml((ROOT / name).read_text())
            doc['image']['tag'] = 'sha-' + self.old; doc['image']['digest'] = D1
            with p.open('w') as stream: values.yaml_tool().dump(doc, stream)
        for name in values.VALUES:
            p = self.cloud / name
            doc, _ = values.parse_yaml((ROOT / name).read_text())
            doc['image']['tag'] = c['image_tag']; doc['image']['digest'] = D2
            with p.open('w') as stream: values.yaml_tool().dump(doc, stream)
        (self.cloud / values.RECORD).write_text(json.dumps(c))
        p = self.cloud / production.BASELINE; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({'schema_version': 1, 'deployments': {t: dict(source_sha=self.old,
            image_digest=D1, confirmed_by='fixture-human', confirmed_at='2026-11-09T00:00:00Z',
            evidence='fixture only; no production assertion') for t in production.TARGETS}}))
        commit(self.cloud)
        return c

    def plan(self, c):
        return production.plan(self.cloud, self.be, c, D2, 'Human fixture: both deployments and functional checks confirmed')

    def auto_context(self, c):
        remote = self.base / 'cloud.git'
        subprocess.run(['git', 'clone', '--bare', '-q', str(self.cloud), str(remote)], check=True)
        git(self.cloud, 'remote', 'add', 'origin', str(remote))
        git(self.be, 'update-ref', 'refs/remotes/origin/main', c['source_sha'])
        event = self.base / 'event.json'
        event.write_text(json.dumps({'workflow_run': {'id': 25, 'run_attempt': 2,
            'name': 'Update staging backend image', 'path': '.github/workflows/backend-staging.yml',
            'head_branch': 'main', 'status': 'completed', 'conclusion': 'success',
            'repository': {'full_name': production.CLOUD_REPO},
            'head_repository': {'full_name': production.CLOUD_REPO}}}))
        summary = self.base / 'summary'
        env = {'GITHUB_EVENT_PATH': str(event), 'GITHUB_STEP_SUMMARY': str(summary),
               'GITHUB_EVENT_NAME': 'workflow_run', 'GITHUB_REPOSITORY': production.CLOUD_REPO}
        result = {'status': 'published', 'cloud_commit': git(self.cloud, 'rev-parse', 'HEAD'),
                  **{k: c[k] for k in ('workflow_run_id', 'workflow_run_attempt', 'source_sha', 'image_digest')}}
        return env, summary, result

    def test_auto_preview_published_candidate(self):
        c = self.setup_cloud(self.change('src/main/java/com/audigo/domain/chat/service/Chat.java'))
        env, summary, result = self.auto_context(c)
        with patch.dict(os.environ, env), patch.object(production, 'staging_result', return_value=result) as fetch, \
                patch.object(production.staging, 'execute', return_value=c), \
                patch.object(production, 'current_staging_candidate', return_value=c), \
                patch.object(production, 'require_approval_environment'):
            production.auto_preview(self.cloud, self.be)
        fetch.assert_called_once_with('25', '2')
        text = summary.read_text()
        self.assertIn(c['source_sha'], text); self.assertIn(D2, text)
        self.assertIn('chat-api', text); self.assertIn('Chat.java', text)
        self.assertIn('아직 확인되지 않았습니다', text)

    def test_auto_preview_accepts_korean_staging_name(self):
        c = self.setup_cloud(self.change('build.gradle'))
        env, summary, _ = self.auto_context(c)
        event = Path(env['GITHUB_EVENT_PATH'])
        payload = json.loads(event.read_text())
        payload['workflow_run']['name'] = 'BE 스테이징 이미지 갱신'
        event.write_text(json.dumps(payload))
        with patch.dict(os.environ, env), patch.object(production, 'staging_result', return_value=None) as fetch:
            production.auto_preview(self.cloud, self.be)
        fetch.assert_called_once_with('25', '2')
        self.assertIn('건너뜁니다', summary.read_text())

    def test_auto_preview_skips_missing_artifact(self):
        c = self.setup_cloud(self.change('build.gradle'))
        env, summary, _ = self.auto_context(c)
        with patch.dict(os.environ, env), patch.object(production, 'staging_result', return_value=None), \
                patch.object(production.staging, 'execute', side_effect=AssertionError('must not validate')):
            production.auto_preview(self.cloud, self.be)
        self.assertIn('건너뜁니다', summary.read_text())

    def test_auto_preview_rejects_replaced_candidate(self):
        c = self.setup_cloud(self.change('build.gradle'))
        env, _, result = self.auto_context(c)
        c['image_digest'] = D1
        (self.cloud / values.RECORD).write_text(json.dumps(c))
        commit(self.cloud)
        with patch.dict(os.environ, env), patch.object(production, 'staging_result', return_value=result), \
                self.assertRaises(values.ValidationError):
            production.auto_preview(self.cloud, self.be)

    def test_approved_candidate_creates_pr(self):
        c = self.setup_cloud(self.change('build.gradle'))
        rows = production.plan(self.cloud, self.be, c, D2, None)
        fingerprint = production.plan_fingerprint(self.cloud, c, rows)
        url = 'https://github.com/100-hours-a-week/KTB4-14th-Cloud/pull/123'
        env = {'GITHUB_STEP_SUMMARY': str(self.base / 'summary'), 'GITHUB_RUN_ID': '25'}
        with patch.dict(os.environ, env), patch.object(production, 'auto_preview', return_value=(c, rows)), \
                patch.object(production, 'create_promotion_pr', return_value=url) as create, \
                patch.object(production, 'request_auto_merge', return_value='자동 병합 요청'):
            production.auto_approved(self.cloud, self.be, fingerprint)
        create.assert_called_once()
        self.assertIn(url, (self.base / 'summary').read_text())

    def test_approved_candidate_changed_is_blocked(self):
        c = self.setup_cloud(self.change('build.gradle'))
        rows = production.plan(self.cloud, self.be, c, D2, None)
        fingerprint = production.plan_fingerprint(self.cloud, c, rows)
        replacement = dict(c, image_digest=D1)
        with patch.object(production, 'auto_preview', return_value=(replacement, rows)), \
                patch.object(production, 'create_promotion_pr') as create, \
                self.assertRaises(values.ValidationError):
            production.auto_approved(self.cloud, self.be, fingerprint)
        create.assert_not_called()

    def test_auto_merge_unconfigured_leaves_pr(self):
        c = self.setup_cloud(self.change('build.gradle'))
        rows = production.plan(self.cloud, self.be, c, D2, None)
        fingerprint = production.plan_fingerprint(self.cloud, c, rows)
        url = 'https://github.com/100-hours-a-week/KTB4-14th-Cloud/pull/123'
        env = {'GITHUB_STEP_SUMMARY': str(self.base / 'summary'), 'GITHUB_RUN_ID': '25'}
        with patch.dict(os.environ, env), patch.object(production, 'auto_preview', return_value=(c, rows)), \
                patch.object(production, 'create_promotion_pr', return_value=url), \
                patch.object(production, 'cloud_gh', side_effect=[json.dumps({'allow_auto_merge': False}), json.dumps({})]) as gh:
            production.auto_approved(self.cloud, self.be, fingerprint)
        self.assertEqual(gh.call_count, 2)
        self.assertIn(url, (self.base / 'summary').read_text())
        self.assertIn('리뷰 대기', (self.base / 'summary').read_text())

    def test_chat_only_and_values_allowlist(self):
        new = self.change('src/main/java/com/audigo/domain/chat/service/ChatService.java')
        c = self.setup_cloud(new); rows = self.plan(c)
        self.assertEqual({r['target'] for r in rows}, set(production.GROUPS['chat'] + production.GROUPS['general']))
        production.update(self.cloud, rows, c)
        production.verify_changes(self.cloud, rows, c)
        (self.cloud / 'unrelated.txt').write_text('forbidden')
        with self.assertRaises(values.ValidationError): production.verify_changes(self.cloud, rows, c)

    def test_travel_pair_and_helm(self):
        new = self.change('src/main/java/com/audigo/domain/travel/service/Worker.java')
        c = self.setup_cloud(new); rows = self.plan(c)
        self.assertTrue(set(production.GROUPS['travel']) <= {r['target'] for r in rows})
        production.update(self.cloud, rows, c)
        body = production.pr_body(rows, c, 'human staging evidence')
        self.assertIn('generation_job_id', body); self.assertIn(D1, body)
        helm = os.environ.get('HELM_BIN') or shutil.which('helm')
        if not helm: self.skipTest('Helm unavailable (other travel assertions executed)')
        for row in rows:
            name = row['target']; namespace = name.split('-')[0] + '-prod'
            subprocess.run([helm, 'lint', str(ROOT / 'apps/chart'), '-f', str(self.cloud / row['file']), '--strict'], check=True, stdout=subprocess.PIPE)
            rendered = subprocess.check_output([helm, 'template', name, str(ROOT / 'apps/chart'), '--namespace', namespace, '-f', str(self.cloud / row['file'])]).decode()
            self.assertIn(D2, rendered)

    def test_common(self):
        c = self.setup_cloud(self.change('build.gradle'))
        rows = self.plan(c)
        self.assertEqual({r['target'] for r in rows}, set(production.TARGETS))
        production.update(self.cloud, rows, c)
        helm = os.environ.get('HELM_BIN') or shutil.which('helm')
        if not helm: self.skipTest('Helm unavailable')
        for row in rows:
            target = row['target']; namespace = target.split('-')[0] + '-prod'
            subprocess.run([helm, 'lint', str(ROOT / 'apps/chart'), '-f', str(self.cloud / row['file']), '--strict'], check=True, stdout=subprocess.PIPE)
            rendered = subprocess.check_output([helm, 'template', target, str(ROOT / 'apps/chart'), '--namespace', namespace, '-f', str(self.cloud / row['file'])]).decode()
            self.assertIn(D2, rendered)

    def test_docs_and_tests_only(self):
        self.change('docs/guide.md'); new = self.change('src/test/java/UnitTest.java')
        c = self.setup_cloud(new)
        self.assertEqual(self.plan(c), [])

    def test_cumulative_unreleased_code(self):
        prior = self.change('src/main/java/com/audigo/domain/chat/controller/Chat.java')
        new = self.change('docs/next.md')
        c = self.setup_cloud(new)
        self.assertNotEqual(prior, self.old)
        self.assertTrue(any('Chat.java' in p for r in self.plan(c) for p in r['paths']))

    def test_delete_and_rename(self):
        path = 'src/main/java/com/audigo/domain/chat/Old.java'
        self.old = self.change(path)
        dest = self.be / 'docs/moved.md'; dest.parent.mkdir(); (self.be / path).rename(dest)
        new = commit(self.be); c = self.setup_cloud(new)
        self.assertTrue(self.plan(c))
        self.assertIn(path, production.changed_paths(self.be, self.old, new))
        self.assertIn('docs/moved.md', production.changed_paths(self.be, self.old, new))

    def test_missing_baseline_and_pending_values(self):
        c = self.setup_cloud(self.change('build.gradle'))
        p = self.cloud / production.BASELINE; original = p.read_text(); p.unlink()
        with self.assertRaises(values.ValidationError): self.plan(c)
        p.write_text(original)
        name = self.cloud / 'apps/spring-value/auth-api-prod.yaml'
        name.write_text(name.read_text().replace(D1, D2))
        with self.assertRaises(values.ValidationError): self.plan(c)

    def test_stale_candidate_older_sha_and_mismatch(self):
        new = self.change('build.gradle'); c = self.setup_cloud(new)
        with self.assertRaises(values.ValidationError): production.changed_paths(self.be, new, self.old)
        with self.assertRaises(values.ValidationError): production.changed_paths(self.be, 'f' * 40, new)
        with self.assertRaises(values.ValidationError): production.plan(self.cloud, self.be, c, D1, 'human staging evidence here')
        p = self.cloud / values.RECORD; p.write_text('{}')
        with self.assertRaises(values.ValidationError): self.plan(c)

    def test_open_pr_block_and_pagination(self):
        with patch.object(production, 'cloud_gh', return_value=json.dumps([[], [{'head': {'ref': 'backend-production/123-1'}, 'title': 'promotion'}]])):
            with self.assertRaises(values.ValidationError): production.no_open_promotion()
        with patch.object(production, 'cloud_gh', return_value='[[]]'):
            production.no_open_promotion()

    def test_validation_only_reuses_run_artifact_registry_without_publication(self):
        import test_backend_staging_integration as fixture
        with patch.object(fixture.staging.retry, 'publish', side_effect=AssertionError('must not publish')):
            client = fixture.Client()
            c = fixture.staging.execute(self.cloud, '123', '2', client, validate_only=True)
            self.assertEqual(c, fixture.candidate())
            self.assertEqual(client.downloads, [7])

    def test_preview_plan_is_read_only_and_staging_mismatch_blocks(self):
        c = self.setup_cloud(self.change('build.gradle'))
        before = git(self.cloud, 'status', '--porcelain')
        self.plan(c)
        self.assertEqual(before, git(self.cloud, 'status', '--porcelain'))
        p = self.cloud / values.VALUES[0]
        p.write_text(p.read_text().replace(D2, D1))
        with self.assertRaises(values.ValidationError): self.plan(c)

    def test_non_image_field_rejected(self):
        c = self.setup_cloud(self.change('build.gradle')); rows = self.plan(c)
        production.update(self.cloud, rows, c)
        p = self.cloud / rows[0]['file']; p.write_text(p.read_text().replace('replicaCount: 1', 'replicaCount: 2'))
        with self.assertRaises(values.ValidationError): production.verify_changes(self.cloud, rows, c)


if __name__ == '__main__': unittest.main()
