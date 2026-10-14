"""Feature-specific import safety checks; no AWS credentials required."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ImportContractTests(unittest.TestCase):
    def test_alb_import_is_disabled_and_has_no_outputs_or_static_targets(self):
        config = json.loads((ROOT / 'deploy/stacks.json').read_text())
        alb = next(s for s in config['stacks'] if s['name'] == 'audigo-alb')
        self.assertFalse(alb['enabled'])
        self.assertFalse(alb['allow_create'])
        self.assertEqual('', alb['role_arn'])
        template = (ROOT / alb['template']).read_text()
        self.assertNotRegex(template, r'(?m)^Outputs:')
        self.assertNotRegex(template, r'(?m)^\s+Targets:')
        self.assertNotIn('Type: AWS::ElasticLoadBalancingV2::ListenerRule', template)
        self.assertEqual(3, template.count('DeletionPolicy: Retain'))
        self.assertEqual(3, template.count('UpdateReplacePolicy: Retain'))


if __name__ == "__main__":
    unittest.main()
