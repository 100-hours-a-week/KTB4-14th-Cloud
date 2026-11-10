"""Unit tests for post-Sync production-baseline proposals; no GitHub or cluster access."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import ai_production as ai
import backend_production as backend
import backend_staging_values as values
import production_baseline as baseline


SHA = '1' * 40
DIGEST = 'sha256:' + 'a' * 64


def init_git(root):
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'config', 'user.email', 'test@example.invalid'], check=True)
    subprocess.run(['git', '-C', str(root), 'config', 'user.name', 'Fixture'], check=True)


class ProductionBaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'cloud'
        self.root.mkdir()
        init_git(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def context(self, service, selected=None):
        selected = selected or baseline.targets(service)
        return {
            'service': service, 'number': 83 if service == 'backend' else 89,
            'merge': '2' * 40, 'url': 'https://example.invalid/pull/83',
            'candidate': {'source_sha': SHA, 'image_digest': DIGEST},
            'targets': {target: path for target, path in baseline.targets(service).items()
                        if target in selected},
        }

    def source_baseline(self, service):
        module = baseline.family(service)
        return baseline.baseline(service, (ROOT / module.BASELINE).read_text())

    def production_checkout(self, service='backend'):
        """A clean main checkout and bare origin, suitable for propose() recovery tests."""
        module = baseline.family(service)
        for name in (module.BASELINE,):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / name, path)
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'main baseline'], check=True)
        remote = self.temp.name + '/origin.git'
        subprocess.run(['git', 'clone', '--bare', '-q', str(self.root), remote], check=True)
        subprocess.run(['git', '-C', str(self.root), 'remote', 'add', 'origin', remote], check=True)
        subprocess.run(['git', '-C', str(self.root), 'fetch', '-q', 'origin', 'main'], check=True)
        return Path(remote)

    def clone_main(self, remote, name):
        checkout = Path(self.temp.name) / name
        subprocess.run(['git', 'clone', '-q', str(remote), str(checkout)], check=True)
        subprocess.run(['git', '-C', str(checkout), 'config', 'user.email', 'test@example.invalid'], check=True)
        subprocess.run(['git', '-C', str(checkout), 'config', 'user.name', 'Fixture'], check=True)
        return checkout

    def promotion_fixture(self, service, number):
        """Create real Git history for a generated promotion PR and its merged main."""
        module = baseline.family(service)
        chosen = {'auth-api'} if service == 'backend' else set(baseline.targets(service))
        paths = set(baseline.targets(service).values()) | {module.BASELINE}
        record = values.RECORD if service == 'backend' else ai.RECORD
        paths.add(record)
        if service == 'ai':
            paths.add(ai.STAGING_VALUES)
        # check_pr also verifies the real Application mapping from main.
        for target in chosen:
            paths.add((f'argocd-apps/applications/spring/{target}-prod.yaml' if service == 'backend'
                       else f'argocd-apps/applications/ai/{target}.yaml'))
        for name in paths:
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / name, destination)
        record = values.RECORD if service == 'backend' else ai.RECORD
        candidate = (values.load_json((self.root / record).read_text()) if service == 'backend'
                     else ai.staging.validate(values.load_json((self.root / record).read_text()),
                                              ai.staging.repository(self.root)))
        old_digest = 'sha256:' + 'f' * 64
        for target in chosen:
            name = baseline.targets(service)[target]
            text = (self.root / name).read_text()
            if service == 'backend':
                doc, _ = values.parse_yaml(text)
                doc['image']['tag'] = 'sha-' + 'f' * 40
                doc['image']['digest'] = old_digest
                import io
                out = io.StringIO(); values.yaml_tool().dump(doc, out)
                (self.root / name).write_text(out.getvalue())
            else:
                doc = ai.staging.yaml_tool().load(text)
                doc['image']['digest'] = old_digest
                import io
                out = io.StringIO(); ai.staging.yaml_tool().dump(doc, out)
                (self.root / name).write_text(out.getvalue())
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'promotion base'], check=True)
        before = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD']).decode().strip()
        for target in chosen:
            name = baseline.targets(service)[target]
            text = (self.root / name).read_text()
            if service == 'backend':
                doc, _ = values.parse_yaml(text)
                doc['image']['tag'] = candidate['image_tag']
                doc['image']['digest'] = candidate['image_digest']
                import io
                out = io.StringIO(); values.yaml_tool().dump(doc, out)
                (self.root / name).write_text(out.getvalue())
            else:
                doc = ai.staging.yaml_tool().load(text)
                doc['image']['digest'] = candidate['image_digest']
                import io
                out = io.StringIO(); ai.staging.yaml_tool().dump(doc, out)
                (self.root / name).write_text(out.getvalue())
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'promotion values'], check=True)
        head = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD']).decode().strip()
        remote = Path(self.temp.name) / (service + '.git')
        subprocess.run(['git', 'clone', '--bare', '-q', str(self.root), str(remote)], check=True)
        subprocess.run(['git', '-C', str(self.root), 'remote', 'add', 'origin', str(remote)], check=True)
        subprocess.run(['git', '-C', str(self.root), 'push', '-q', 'origin', f'{head}:refs/pull/{number}/head'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'fetch', '-q', 'origin', 'main'], check=True)
        return before, head, candidate, chosen, remote

    def promotion_api(self, service, number, before, head, baseline_pr=None):
        workflow = f'.github/workflows/{service}-production.yml'
        def fake(path):
            if path == f'pulls/{number}':
                return {'merged': True, 'base': {'ref': 'main', 'sha': before, 'repo': {'full_name': baseline.CLOUD}},
                        'head': {'ref': f'{service}-production/55-1', 'sha': head,
                                 'repo': {'full_name': baseline.CLOUD}}, 'merge_commit_sha': head}
            if path == f'actions/workflows/{service}-production.yml':
                return {'path': workflow, 'id': 7}
            if path == 'actions/runs/55/attempts/1':
                return {'workflow_id': 7, 'path': workflow, 'id': 55, 'run_attempt': 1,
                        'event': 'workflow_dispatch', 'head_branch': 'main', 'head_sha': before,
                        'status': 'completed', 'conclusion': 'success',
                        'repository': {'full_name': baseline.CLOUD}, 'head_repository': {'full_name': baseline.CLOUD}}
            if path == 'git/ref/heads/main':
                return {'object': {'sha': head}}
            if baseline_pr and path == 'pulls/101':
                return baseline_pr
            raise AssertionError('unexpected API path: ' + path)
        return fake

    def observations(self, context):
        return {'apps': {target: {
            'application': target, 'namespace': target + '-prod',
            'sync_status': 'Synced', 'health_status': 'Healthy', 'rollout_complete': True,
            'revision': '3' * 40,
            'pods': [{'ready': True, 'deleting': False, 'image_id': 'repo@' + DIGEST}],
        } for target in context['targets']}}

    def test_normal_backend_and_ai_updates_only_selected_entries(self):
        for service in ('backend', 'ai'):
            with self.subTest(service=service):
                before = self.source_baseline(service)
                context = self.context(service)
                observed = self.observations(context)
                after = baseline.updated_baseline(service, before, context, observed,
                    '담당자가 운영 상태와 기능을 확인했습니다.', 'reviewer', '2026-11-10T00:00:00Z')
                self.assertEqual(set(after['deployments']), set(before['deployments']))
                for target in context['targets']:
                    self.assertEqual(after['deployments'][target]['source_sha'], SHA)
                    self.assertEqual(after['deployments'][target]['image_digest'], DIGEST)
                    proof = values.load_json(after['deployments'][target]['evidence'])
                    self.assertEqual(proof['promotion_pr'], context['number'])

    def test_backend_subset_changes_only_observed_target(self):
        before = self.source_baseline('backend')
        context = self.context('backend', {'auth-api'})
        after = baseline.updated_baseline('backend', before, context, self.observations(context),
            '담당자가 auth-api 운영 기능을 확인했습니다.', 'reviewer', '2026-11-10T00:00:00Z')
        changed = {target for target in before['deployments']
                   if before['deployments'][target] != after['deployments'][target]}
        self.assertEqual(changed, {'auth-api'})

    def test_missing_observation_and_runtime_digest_are_rejected(self):
        context = self.context('ai', {'match-ai'})
        with self.assertRaises(values.ValidationError):
            baseline.verify_observations(self.root, context, {'apps': {}}, 'functional proof')
        bad = self.observations(context)
        bad['apps']['match-ai']['pods'][0]['image_id'] = 'repo@sha256:' + 'b' * 64
        with patch.object(baseline, 'application', return_value=('match-ai', 'match-ai-prod')), \
                patch.object(baseline, 'git', return_value='4' * 40), \
                patch.object(baseline, 'ancestor'), \
                patch.object(baseline, 'read', return_value='image:\n  repository: example\n  digest: ' + DIGEST + '\n'):
            with self.assertRaises(values.ValidationError):
                baseline.verify_observations(self.root, context, bad, 'functional proof')

    def test_index_maps_platform_digest_only_when_raw_bytes_match_candidate(self):
        raw = json.dumps({'schemaVersion': 2,
            'mediaType': 'application/vnd.oci.image.index.v1+json',
            'manifests': [{'digest': 'sha256:' + 'c' * 64,
                           'platform': {'os': 'linux', 'architecture': 'arm64'}}]}, separators=(',', ':'))
        candidate = {'image_digest': 'sha256:' + __import__('hashlib').sha256(raw.encode()).hexdigest()}
        self.assertIn('sha256:' + 'c' * 64, baseline.runtime_digests({'image_index': raw}, candidate))
        with self.assertRaises(values.ValidationError):
            baseline.runtime_digests({'image_index': raw}, {'image_digest': DIGEST})

    def test_rollback_or_stale_values_are_rejected_by_observation_comparison(self):
        context = self.context('ai', {'match-ai'})
        observed = self.observations(context)
        old = 'image:\n  repository: example\n  digest: sha256:' + 'b' * 64 + '\n'
        new = 'image:\n  repository: example\n  digest: ' + DIGEST + '\n'
        with patch.object(baseline, 'application', return_value=('match-ai', 'match-ai-prod')), \
                patch.object(baseline, 'git', side_effect=['4' * 40]), \
                patch.object(baseline, 'ancestor'), \
                patch.object(baseline, 'read', side_effect=[old, new]):
            with self.assertRaises(values.ValidationError):
                baseline.verify_observations(self.root, context, observed, 'functional proof')

    def test_applied_and_repeat_open_pr_are_idempotent(self):
        context = self.context('ai', {'match-ai'})
        before = self.source_baseline('ai')
        proposed = baseline.updated_baseline('ai', before, context, self.observations(context),
            '담당자가 운영 기능을 확인했습니다.', 'reviewer', '2026-11-10T00:00:00Z')
        self.assertTrue(baseline.applied(proposed, context))
        self.assertFalse(baseline.applied(before, context))

    def test_verify_proposal_rejects_extra_target_change(self):
        before = self.source_baseline('ai')
        context = self.context('ai', {'match-ai'})
        proposed = baseline.updated_baseline('ai', before, context, self.observations(context),
            '담당자가 운영 기능을 확인했습니다.', 'reviewer', '2026-11-10T00:00:00Z')
        proposed['deployments']['travel-ai']['source_sha'] = SHA
        with self.assertRaises(values.ValidationError):
            baseline.verify_proposal(self.root, context, before, proposed)

    def test_propose_creates_one_json_pr_and_repeat_returns_open_link(self):
        remote = self.production_checkout()
        context = self.context('backend', {'auth-api'})
        observed = self.observations(context)
        url = 'https://example.invalid/pull/101'
        main_sha = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'origin/main']).decode().strip()
        with patch.object(baseline, 'assert_main'), patch.object(baseline, 'trusted_promotion', return_value=context), \
                patch.object(baseline, 'verify_observations'), patch.object(baseline, 'verify_proposal'), \
                patch.object(baseline, 'existing_pr', side_effect=[None, None]), \
                patch.object(baseline, 'api', return_value={'object': {'sha': main_sha}}), \
                patch.object(baseline, 'gh', return_value=url):
            self.assertEqual(baseline.propose(self.root, 'backend', '83', observed, 'functional proof', 'reviewer'), url)
        branch = 'refs/heads/production-baseline/backend-pr-83'
        saved = subprocess.check_output(['git', '--git-dir', str(remote), 'rev-parse', branch]).decode().strip()
        changed = subprocess.check_output(['git', '--git-dir', str(remote), 'diff-tree', '--no-commit-id', '--name-only', '-r', saved]).decode().splitlines()
        self.assertEqual(changed, [backend.BASELINE])
        retry = self.clone_main(remote, 'retry')
        existing = {'state': 'open', 'html_url': url, 'number': 101}
        with patch.object(baseline, 'assert_main'), patch.object(baseline, 'trusted_promotion', return_value=context), \
                patch.object(baseline, 'verify_observations'), patch.object(baseline, 'check_pr') as check, \
                patch.object(baseline, 'existing_pr', return_value=existing):
            self.assertEqual(baseline.propose(retry, 'backend', '83', observed, 'functional proof', 'reviewer'), url)
        check.assert_called_once_with(retry, 101)

    def test_retry_after_push_recovers_branch_without_second_commit(self):
        remote = self.production_checkout()
        context = self.context('backend', {'auth-api'})
        observed = self.observations(context)
        common = dict(assert_main=patch.object(baseline, 'assert_main'),
                      trusted=patch.object(baseline, 'trusted_promotion', return_value=context),
                      observed=patch.object(baseline, 'verify_observations'),
                      proposal=patch.object(baseline, 'verify_proposal'))
        main_sha = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'origin/main']).decode().strip()
        with common['assert_main'], common['trusted'], common['observed'], common['proposal'], \
                patch.object(baseline, 'existing_pr', side_effect=[None, None]), \
                patch.object(baseline, 'api', return_value={'object': {'sha': main_sha}}), \
                patch.object(baseline, 'gh', side_effect=values.ValidationError('PR API unavailable')):
            with self.assertRaises(values.ValidationError):
                baseline.propose(self.root, 'backend', '83', observed, 'functional proof', 'reviewer')
        ref = 'refs/heads/production-baseline/backend-pr-83'
        first = subprocess.check_output(['git', '--git-dir', str(remote), 'rev-parse', ref]).decode().strip()
        retry = self.clone_main(remote, 'recovery')
        retry_main = subprocess.check_output(['git', '-C', str(retry), 'rev-parse', 'origin/main']).decode().strip()
        with patch.object(baseline, 'assert_main'), patch.object(baseline, 'trusted_promotion', return_value=context), \
                patch.object(baseline, 'verify_observations'), patch.object(baseline, 'verify_proposal'), \
                patch.object(baseline, 'existing_pr', side_effect=[None, None]), \
                patch.object(baseline, 'api', return_value={'object': {'sha': retry_main}}), \
                patch.object(baseline, 'gh', return_value='https://example.invalid/pull/102'):
            baseline.propose(retry, 'backend', '83', observed, 'functional proof', 'reviewer')
        second = subprocess.check_output(['git', '--git-dir', str(remote), 'rev-parse', ref]).decode().strip()
        self.assertEqual(first, second)

    def test_trusted_promotion_rejects_non_generated_branch_before_git_access(self):
        pr = {'merged': True, 'base': {'ref': 'main', 'repo': {'full_name': baseline.CLOUD}},
              'head': {'ref': 'manual/change', 'repo': {'full_name': baseline.CLOUD}}}
        with patch.object(baseline, 'api', return_value=pr):
            with self.assertRaises(values.ValidationError):
                baseline.trusted_promotion(self.root, 'backend', '83')

    def test_trusted_promotion_uses_real_git_diff_for_backend_subset_and_ai(self):
        for service, number in (('backend', 83), ('ai', 89)):
            with self.subTest(service=service):
                if service == 'ai':
                    self.root = Path(self.temp.name) / 'ai-cloud'
                    self.root.mkdir()
                    init_git(self.root)
                before, head, candidate, chosen, _ = self.promotion_fixture(service, number)
                with patch.object(baseline, 'api', side_effect=self.promotion_api(service, number, before, head)):
                    context = baseline.trusted_promotion(self.root, service, str(number))
                self.assertEqual(set(context['targets']), chosen)
                self.assertEqual(context['candidate'], candidate)

    def test_check_pr_accepts_real_baseline_branch_then_rejects_rollback(self):
        before, head, candidate, chosen, remote = self.promotion_fixture('backend', 83)
        with patch.object(baseline, 'api', side_effect=self.promotion_api('backend', 83, before, head)):
            context = baseline.trusted_promotion(self.root, 'backend', '83')
        target = 'auth-api'
        app, namespace = baseline.application(self.root, 'backend', target)
        observations = {'apps': {target: {'application': app, 'namespace': namespace,
            'sync_status': 'Synced', 'health_status': 'Healthy', 'rollout_complete': True, 'revision': head,
            'pods': [{'ready': True, 'deleting': False, 'image_id': 'repo@' + candidate['image_digest']}]}}}
        current = baseline.baseline('backend', (self.root / backend.BASELINE).read_text())
        proposed = baseline.updated_baseline('backend', current, context, observations,
            '담당자가 운영 인증 기능을 확인했습니다.', 'reviewer', '2026-11-10T00:00:00Z')
        subprocess.run(['git', '-C', str(self.root), 'switch', '-q', '-c', 'production-baseline/backend-pr-83'], check=True)
        (self.root / backend.BASELINE).write_text(json.dumps(proposed, ensure_ascii=False, indent=2) + '\n')
        subprocess.run(['git', '-C', str(self.root), 'add', backend.BASELINE], check=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'baseline branch'], check=True)
        proposal_head = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD']).decode().strip()
        subprocess.run(['git', '-C', str(self.root), 'push', '-q', 'origin', f'{proposal_head}:refs/pull/101/head'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'switch', '-q', 'main'], check=True)
        marker = json.dumps({'schema_version': 1, 'service': 'backend', 'promotion_pr': 83})
        pr101 = {'number': 101, 'html_url': 'https://example.invalid/pull/101',
                 'base': {'ref': 'main', 'repo': {'full_name': baseline.CLOUD}},
                 'head': {'ref': 'production-baseline/backend-pr-83', 'sha': proposal_head,
                          'repo': {'full_name': baseline.CLOUD}},
                 'body': '<!-- production-baseline:' + marker + ' -->'}
        api = self.promotion_api('backend', 83, before, head, pr101)
        with patch.object(baseline, 'api', side_effect=api):
            baseline.check_pr(self.root, 101)
        # Revert the value after the baseline PR was opened: freshness validation must block it.
        name = baseline.targets('backend')[target]
        subprocess.run(['git', '-C', str(self.root), 'checkout', head, '--', name], check=True)
        text = (self.root / name).read_text().replace(candidate['image_digest'], 'sha256:' + 'b' * 64)
        (self.root / name).write_text(text)
        subprocess.run(['git', '-C', str(self.root), 'add', name], check=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'rollback values'], check=True)
        rollback = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD']).decode().strip()
        api = self.promotion_api('backend', 83, before, head, pr101)
        api_original = api
        def rolled(path):
            data = api_original(path)
            if path == 'git/ref/heads/main':
                return {'object': {'sha': rollback}}
            return data
        with patch.object(baseline, 'api', side_effect=rolled):
            with self.assertRaises(values.ValidationError):
                baseline.check_pr(self.root, 101)


if __name__ == '__main__':
    unittest.main()
