"""Localized workflow names retain historical runs and reject unrelated runs."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ai_staging
import ai_production


class WorkflowNames(unittest.TestCase):
    def test_ai_publish_names(self):
        sha = 'a' * 40
        run = dict(id=123, run_attempt=2, workflow_id=9, path=ai_staging.WORKFLOW,
                   event='push', head_branch='main', status='completed', conclusion='success',
                   head_sha=sha, repository={'full_name': ai_staging.SOURCE},
                   head_repository={'full_name': ai_staging.SOURCE})
        candidate = dict(source_sha=sha, workflow_run_id='123', workflow_run_attempt='2')
        for name in ('AI Image CD', 'AI 이미지 발행', 'Unrelated workflow'):
            with self.subTest(name=name):
                client = Mock()
                client.api.side_effect = [dict(id=9, path=ai_staging.WORKFLOW),
                                          dict(run, name=name),
                                          dict(total_count=1, artifacts=[dict(id=7, expired=False,
                                               name=f'ai-image-candidate-{sha}-123-2')])]
                with patch.object(ai_staging, 'repository', return_value='fixture'), \
                     patch.object(ai_staging, 'candidate_zip', return_value=candidate):
                    if name == 'Unrelated workflow':
                        with self.assertRaises(ai_staging.Error):
                            ai_staging.fetch_candidate(Path('.'), '123', '2', client)
                    else:
                        self.assertEqual(ai_staging.fetch_candidate(Path('.'), '123', '2', client), candidate)

    def test_ai_staging_names(self):
        run = dict(id=123, run_attempt=2, workflow_id=9, path=ai_production.STAGING_WORKFLOW,
                   event='workflow_dispatch', head_branch='main', status='completed', conclusion='success',
                   repository={'full_name': ai_production.CLOUD_REPO},
                   head_repository={'full_name': ai_production.CLOUD_REPO})
        for name in ('Update staging AI image', 'AI 스테이징 이미지 갱신', 'Unrelated workflow'):
            with self.subTest(name=name), patch.object(ai_production, 'cloud_api', side_effect=[
                    json.dumps(dict(id=9, path=ai_production.STAGING_WORKFLOW)),
                    json.dumps(dict(run, name=name))]):
                if name == 'Unrelated workflow':
                    with self.assertRaises(ai_staging.Error):
                        ai_production.trusted_staging_run('123', '2')
                else:
                    self.assertEqual(ai_production.trusted_staging_run('123', '2')['name'], name)
