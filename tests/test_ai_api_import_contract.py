"""AI Worker Service IMPORT 계약을 검증한다. AWS에 접근하지 않는다.

관리 경계: Service 설정은 이 스택, Task Definition·활성 Worker 버전은 ai-deploy.yml.
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE_ARN = 'arn:aws:ecs:ap-northeast-2:185489190599:service/audigo-ecs-cluster/audigo-mock-ai-service'


class AiApiImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from cfnlint.decode import decode
        except ImportError:
            raise unittest.SkipTest('cfn-lint is installed in CI')
        cls.template, errors = decode(str(ROOT / 'cloudformation/ai-api.yaml'))
        assert not errors, errors
        cls.iam, errors = decode(str(ROOT / 'bootstrap/infra-iam.yaml'))
        assert not errors, errors
        cls.config = json.loads((ROOT / 'deploy/stacks.json').read_text())
        cls.stack = next(s for s in cls.config['stacks'] if s['name'] == 'audigo-ai-api')

    def test_only_existing_service_is_declared_and_retained(self):
        resources = self.template['Resources']
        self.assertEqual(['AudigoAiService'], list(resources))
        service = resources['AudigoAiService']
        self.assertEqual('AWS::ECS::Service', service['Type'])
        self.assertEqual(('Retain', 'Retain'), (service['DeletionPolicy'], service['UpdateReplacePolicy']))
        self.assertNotIn('Parameters', self.template)
        self.assertNotIn('Outputs', self.template)

    def test_task_definition_and_count_are_left_to_ai_deploy(self):
        props = self.template['Resources']['AudigoAiService']['Properties']
        self.assertEqual('audigo-mock-ai', props['TaskDefinition'])  # family만, 리비전 고정 금지
        self.assertNotIn('DesiredCount', props)
        text = (ROOT / 'cloudformation/ai-api.yaml').read_text(encoding='utf-8')
        self.assertNotIn('Type: AWS::ECS::TaskDefinition', text)
        self.assertNotIn('task-definition/audigo-mock-ai:', text)
        self.assertNotIn('AWS::SSM::Parameter', text)

    def test_service_preserves_live_configuration(self):
        props = self.template['Resources']['AudigoAiService']['Properties']
        self.assertEqual('audigo-ecs-cluster', props['Cluster'])
        self.assertEqual('audigo-mock-ai-service', props['ServiceName'])
        self.assertEqual('EC2', props['LaunchType'])
        self.assertEqual({'Type': 'ECS'}, props['DeploymentController'])
        deployment = props['DeploymentConfiguration']
        self.assertEqual((100, 0), (deployment['MaximumPercent'], deployment['MinimumHealthyPercent']))
        self.assertEqual({'Enable': True, 'Rollback': True, 'ResetOnHealthyTask': False,
                          'ThresholdConfiguration': {'Type': 'BOUNDED_PERCENT', 'Value': 50}},
                         deployment['DeploymentCircuitBreaker'])
        self.assertEqual([{'Type': 'spread', 'Field': 'attribute:ecs.availability-zone'},
                          {'Type': 'spread', 'Field': 'instanceId'}], props['PlacementStrategies'])
        for absent in ('NetworkConfiguration', 'LoadBalancers', 'ServiceRegistries', 'PlacementConstraints',
                       'PropagateTags', 'CapacityProviderStrategy', 'PlatformVersion'):
            self.assertNotIn(absent, props)

    def test_import_identifier_matches_service(self):
        (entry,) = json.loads((ROOT / 'deploy/imports/ai-api-resources-to-import.json').read_text())
        self.assertEqual('AudigoAiService', entry['LogicalResourceId'])
        self.assertEqual('AWS::ECS::Service', entry['ResourceType'])
        self.assertEqual(SERVICE_ARN, entry['ResourceIdentifier']['ServiceArn'])

    def test_stack_is_app_group_and_never_created_by_cd(self):
        self.assertEqual('app', self.stack['role_group'])
        self.assertTrue(self.stack['enabled'])
        self.assertFalse(self.stack['allow_create'])  # 스택은 관리자 IMPORT로만 만든다
        self.assertEqual([], json.loads((ROOT / self.stack['parameters']).read_text()))

    def test_app_role_manages_only_the_ai_service(self):
        statements = [s for p in self.iam['Resources']['AudigoCfnAppExecutionRole']['Properties']['Policies']
                      for s in p['PolicyDocument']['Statement']]
        sids = {s['Sid']: s for s in statements}
        for sid in ('ReadManagedAiService', 'UpdateManagedAiService'):
            self.assertTrue(sids[sid]['Resource']['Fn::Sub'].endswith(
                ':service/audigo-ecs-cluster/audigo-mock-ai-service'))
        actions = set()
        for s in statements:
            if 'audigo-mock-ai' in json.dumps(s):
                actions |= {s['Action']} if isinstance(s['Action'], str) else set(s['Action'])
        self.assertNotIn('ecs:RegisterTaskDefinition', actions)
        self.assertNotIn('ecs:DeleteService', actions)
        self.assertNotIn('ecs:CreateService', actions)


if __name__ == '__main__':
    unittest.main()
