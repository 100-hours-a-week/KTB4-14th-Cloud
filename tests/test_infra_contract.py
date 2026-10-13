"""Repository-wide invariants; no AWS credentials or extra packages required."""
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('infra_contract', ROOT / 'scripts/infra.py')
infra = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(infra)


class ContractTests(unittest.TestCase):
    def test_all_templates_registered(self):
        _, stacks, _ = infra.validate_config(ROOT / 'deploy/stacks.json')
        actual = list((ROOT / 'cloudformation').rglob('*.yaml')) + list((ROOT / 'cloudformation').rglob('*.yml'))
        self.assertEqual(len(actual), len(stacks))

    def test_new_workflows_pin_every_action_to_full_sha(self):
        for path in (ROOT / '.github/workflows').glob('cloudformation-*.yml'):
            actions = re.findall(r'uses:\s*([^\s#]+)', path.read_text())
            self.assertTrue(actions)
            for action in actions:
                self.assertRegex(action, r'^[\w-]+/[\w-]+@[a-f0-9]{40}$')

    def test_ci_has_no_aws_authentication(self):
        text = (ROOT / '.github/workflows/cloudformation-ci.yml').read_text()
        self.assertNotIn('id-token:', text)
        self.assertNotIn('configure-aws-credentials', text)
        self.assertNotIn('pull_request_target', text)

    def test_cd_auto_plan_and_approval_gate(self):
        text = (ROOT / '.github/workflows/cloudformation-cd.yml').read_text()
        self.assertIn('  push:\n    branches: [main]', text)
        self.assertIn('default: all', text)
        self.assertIn('environment: iac-plan', text)
        self.assertIn('environment: iac-production', text)
        self.assertIn("if: needs.plan.outputs.has_changes == 'true'", text)
        self.assertIn('scripts/infra_batch.py plan', text)
        self.assertIn('scripts/infra_batch.py apply', text)

    def test_iam_examples_are_json_and_separate_plan_apply(self):
        policies = {}
        for path in (ROOT / 'deploy/iam').glob('*.json'):
            policies[path.name] = json.loads(path.read_text())
        def actions(name):
            return {a for s in policies[name]['Statement'] for a in (s['Action'] if isinstance(s['Action'], list) else [s['Action']])}
        self.assertNotIn('cloudformation:ExecuteChangeSet', actions('github-plan-policy.json'))
        self.assertNotIn('cloudformation:CreateChangeSet', actions('github-apply-policy.json'))
        self.assertNotIn('iam:PassRole', actions('github-apply-policy.json'))
        for name in ['github-plan-policy.json', 'github-apply-policy.json']:
            self.assertNotIn('cloudformation:DeleteStack', actions(name))
            self.assertNotIn('*', actions(name))
        self.assertEqual({'ec2:CreateTags', 'ec2:DeleteTags'}, {a for a in actions('network-execution-policy.json') if not a.startswith('ec2:Describe')})


if __name__ == '__main__':
    unittest.main()
