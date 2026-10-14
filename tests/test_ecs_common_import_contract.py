"""Existing ECS cluster import safety checks; no AWS credentials required."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class EcsCommonImportTests(unittest.TestCase):
    def test_ecs_common_preserves_clusters_and_uses_platform_cd(self):
        config = json.loads((ROOT / 'deploy/stacks.json').read_text())
        stack = next(s for s in config['stacks'] if s['name'] == 'audigo-ecs-common')
        self.assertTrue(stack['enabled'])
        self.assertFalse(stack['allow_create'])
        self.assertEqual('platform', stack['role_group'])
        self.assertNotIn('role_arn', stack)
        self.assertEqual([], json.loads((ROOT / stack['parameters']).read_text()))
        template = (ROOT / stack['template']).read_text()
        self.assertEqual(2, template.count('Type: AWS::ECS::Cluster\n'))
        self.assertEqual(2, template.count('DeletionPolicy: Retain'))
        self.assertEqual(2, template.count('UpdateReplacePolicy: Retain'))
        for name in ('audigo-ecs-cluster', 'audigo-staging-ecs-cluster'):
            self.assertIn('ClusterName: ' + name + '\n', template)
        self.assertEqual(2, template.count('DefaultCapacityProviderStrategy: []'))
        self.assertEqual(2, template.count('- FARGATE\n'))
        self.assertEqual(2, template.count('- FARGATE_SPOT\n'))
        self.assertEqual(2, template.count('Name: containerInsights\n'))
        self.assertEqual(2, template.count('Value: disabled\n'))
        self.assertEqual(2, template.count('Logging: DEFAULT\n'))
        self.assertIn('Key: name\n          Value: ecs-on-ec2', template)
        self.assertIn('Key: version\n          Value: v1', template)
        resource_types = re.findall(r'(?m)^    Type: (AWS::[^\n]+)$', template)
        self.assertEqual(['AWS::ECS::Cluster', 'AWS::ECS::Cluster'], resource_types)
        self.assertNotRegex(template, r'(?m)^(Outputs|Parameters):')


if __name__ == "__main__":
    unittest.main()
