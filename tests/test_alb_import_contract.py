"""Feature-specific import safety checks; no AWS credentials required."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ImportContractTests(unittest.TestCase):
    def test_alb_cd_uses_platform_and_preserves_import_safety(self):
        config = json.loads((ROOT / 'deploy/stacks.json').read_text())
        alb = next(s for s in config['stacks'] if s['name'] == 'audigo-alb')
        # 배포 활성화는 운영 설정이며, 편입 리소스 보존 조건과 분리한다.
        self.assertFalse(alb['allow_create'])
        self.assertEqual('platform', alb['role_group'])
        self.assertNotIn('role_arn', alb)
        template = (ROOT / alb['template']).read_text()
        self.assertNotRegex(template, r'(?m)^Outputs:')
        self.assertNotRegex(template, r'(?m)^\s+Targets:')
        self.assertNotIn('Type: AWS::ElasticLoadBalancingV2::ListenerRule', template)
        self.assertEqual(3, template.count('DeletionPolicy: Retain'))
        self.assertEqual(3, template.count('UpdateReplacePolicy: Retain'))


if __name__ == "__main__":
    unittest.main()
