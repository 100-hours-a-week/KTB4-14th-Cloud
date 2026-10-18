"""REST import phases and production-setting preservation; no AWS access."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RestApiImportTests(unittest.TestCase):
    def test_phase_templates_pass_cfn_lint_when_installed(self):
        executable = shutil.which("cfn-lint")
        if not executable:
            self.skipTest("cfn-lint unavailable locally; installed in GitHub CI")
        paths = [str(ROOT / f"deploy/imports/rest-api-{name}.json")
                 for name in ("task", "service", "scalable-target")]
        paths.append(str(ROOT / 'cloudformation/rest-api.yaml'))
        result = subprocess.run([executable, "-t", *paths], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def final(self):
        import yaml  # Installed transitively with cfn-lint in CI.
        return yaml.safe_load((ROOT / 'cloudformation/rest-api.yaml').read_text())

    def test_final_yaml_leaves_task_definition_to_backend_cd(self):
        # 2026-10-18 관리 범위 변경: Task Definition은 BE 레포 CD, 태스크 개수는 Auto Scaling이 관리한다.
        try:
            final = self.final()
        except ImportError:
            self.skipTest("PyYAML unavailable locally; installed with cfn-lint in CI")
        imported = self.phase('scalable-target')['Resources']
        self.assertEqual({'AudigoRestService', 'AudigoRestScalableTarget'}, set(final['Resources']))
        self.assertNotIn('Parameters', final)
        self.assertEqual(imported['AudigoRestScalableTarget'], final['Resources']['AudigoRestScalableTarget'])

        service = final['Resources']['AudigoRestService']
        props = service['Properties']
        self.assertEqual('audigo-backend-Fargate', props['TaskDefinition'])  # family만, 리비전 고정 금지
        self.assertNotIn('DesiredCount', props)
        self.assertEqual(('Retain', 'Retain'), (service['DeletionPolicy'], service['UpdateReplacePolicy']))
        # TaskDefinition·DesiredCount를 제외한 Service 설정은 편입 당시 값 그대로 유지한다.
        expected = {k: v for k, v in imported['AudigoRestService']['Properties'].items()
                    if k not in ('TaskDefinition', 'DesiredCount')}
        self.assertEqual(expected, {k: v for k, v in props.items() if k != 'TaskDefinition'})

    def phase(self, name):
        return json.loads((ROOT / f'deploy/imports/rest-api-{name}.json').read_text())

    def test_import_phases_only_add_one_existing_resource(self):
        task, service, scaling = [self.phase(n) for n in ('task', 'service', 'scalable-target')]
        self.assertEqual({'AudigoRestTaskDefinition'}, set(task['Resources']))
        self.assertEqual({'AudigoRestTaskDefinition', 'AudigoRestService'}, set(service['Resources']))
        self.assertEqual(set(service['Resources']) | {'AudigoRestScalableTarget'}, set(scaling['Resources']))
        self.assertEqual(task['Resources']['AudigoRestTaskDefinition'], service['Resources']['AudigoRestTaskDefinition'])
        for key, value in service['Resources'].items():
            self.assertEqual(value, scaling['Resources'][key])
        for body in (task, service, scaling):
            self.assertNotIn('Outputs', body)
            self.assertNotIn('Parameters', body)
            for resource in body['Resources'].values():
                self.assertEqual('Retain', resource['DeletionPolicy'])
                self.assertEqual('Retain', resource['UpdateReplacePolicy'])
            self.assertNotIn('AWS::IAM::Role', {r['Type'] for r in body['Resources'].values()})
            self.assertNotIn('AWS::ApplicationAutoScaling::ScalingPolicy', {r['Type'] for r in body['Resources'].values()})

    def test_task_preserves_existing_fargate_configuration(self):
        props = self.phase('task')['Resources']['AudigoRestTaskDefinition']['Properties']
        self.assertEqual('audigo-backend-Fargate', props['Family'])
        self.assertEqual(('1024', '2048'), (props['Cpu'], props['Memory']))
        self.assertEqual({'CpuArchitecture': 'ARM64', 'OperatingSystemFamily': 'LINUX'}, props['RuntimePlatform'])
        self.assertEqual(['FARGATE'], props['RequiresCompatibilities'])
        self.assertEqual('awsvpc', props['NetworkMode'])
        self.assertNotIn('TaskRoleArn', props)
        self.assertEqual('arn:aws:iam::185489190599:role/audigo-ecs-task-execution-role', props['ExecutionRoleArn'])
        container = props['ContainerDefinitions'][0]
        self.assertEqual('spring-boot', container['Name'])
        self.assertTrue(container['Image'].endswith('@sha256:620387c453cedc0fdd05c4dd661c9a35bbd65a1e0644b70b7b3e91dfffedcf70'))
        env = {e['Name']: e['Value'] for e in container['Environment']}
        self.assertEqual('10.0.7.252', env['DB_HOST'])
        self.assertEqual('http://10.0.15.32:8001', env['AUDIGO_AI_BASE_URL'])
        self.assertEqual('https://audigo.kr/', env['AUDIGO_CORS_ALLOWED_ORIGINS'])
        self.assertEqual('update', env['JPA_DDL_AUTO'])
        self.assertNotIn('DB_PASSWORD', env)
        self.assertEqual(7, len(container['Secrets']))
        self.assertTrue(all(s['ValueFrom'].startswith('arn:aws:secretsmanager:') for s in container['Secrets']))
        self.assertEqual('/ecs/audigo-backend-Fargate', container['LogConfiguration']['Options']['awslogs-group'])
        self.assertEqual(120, container['HealthCheck']['StartPeriod'])

    def test_service_keeps_live_network_and_alb(self):
        props = self.phase('service')['Resources']['AudigoRestService']['Properties']
        self.assertEqual('audigo-backend-Fargate-service', props['ServiceName'])
        self.assertEqual({'Ref': 'AudigoRestTaskDefinition'}, props['TaskDefinition'])
        self.assertEqual('FARGATE', props['LaunchType'])
        self.assertEqual('1.4.0', props['PlatformVersion'])
        self.assertEqual(1, props['DesiredCount'])
        self.assertEqual({'AssignPublicIp': 'ENABLED', 'SecurityGroups': ['sg-0da962a4706dacd5f'], 'Subnets': ['subnet-0b9604514010cbb54', 'subnet-06bb5908818e1bc74']}, props['NetworkConfiguration']['AwsvpcConfiguration'])
        self.assertEqual(8080, props['LoadBalancers'][0]['ContainerPort'])
        self.assertTrue(props['LoadBalancers'][0]['TargetGroupArn'].endswith('targetgroup/audigo-v1-Fargate/53f2677044a1644f'))
        self.assertEqual(100, props['DeploymentConfiguration']['MinimumHealthyPercent'])
        self.assertEqual(200, props['DeploymentConfiguration']['MaximumPercent'])
        self.assertNotIn('Role', props)
        self.assertNotIn('CapacityProviderStrategy', props)

    def test_scalable_target_preserves_bounds_without_policies(self):
        props = self.phase('scalable-target')['Resources']['AudigoRestScalableTarget']['Properties']
        self.assertEqual((1, 2), (props['MinCapacity'], props['MaxCapacity']))
        self.assertEqual('service/audigo-ecs-cluster/audigo-backend-Fargate-service', props['ResourceId'])
        self.assertEqual('ecs:service:DesiredCount', props['ScalableDimension'])
        self.assertEqual({'DynamicScalingInSuspended': False, 'DynamicScalingOutSuspended': False, 'ScheduledScalingSuspended': False}, props['SuspendedState'])

    def test_service_omits_none_tag_propagation_for_cloudformation(self):
        # ECS reports NONE, but CloudFormation only permits SERVICE/TASK_DEFINITION.
        # Omission preserves the default NONE without changing managed tags.
        for name in ('service', 'scalable-target'):
            with self.subTest(phase=name):
                props = self.phase(name)['Resources']['AudigoRestService']['Properties']
                self.assertNotIn('PropagateTags', props)
                self.assertTrue(props['EnableECSManagedTags'])

    def test_final_yaml_preserves_imported_resources_and_app_role_group(self):
        text = (ROOT / 'cloudformation/rest-api.yaml').read_text()
        self.assertNotIn('Type: AWS::ECS::TaskDefinition', text)
        self.assertNotIn('DesiredCount:', text)
        self.assertNotIn('task-definition/audigo-backend-Fargate:', text)
        self.assertIn('Type: AWS::ECS::Service', text)
        self.assertIn('Type: AWS::ApplicationAutoScaling::ScalableTarget', text)
        for logical_id in ('AudigoRestService', 'AudigoRestScalableTarget'):
            self.assertIn(f'  {logical_id}:', text)
        self.assertNotIn('PropagateTags:', text)
        self.assertNotIn('\nOutputs:', text)
        config = json.loads((ROOT / 'deploy/stacks.json').read_text())
        stack = next(s for s in config['stacks'] if s['name'] == 'audigo-rest-api')
        # CD 활성화 여부는 운영 설정이며 기존 리소스 보존 계약과 별개입니다.
        self.assertFalse(stack['allow_create'])
        self.assertEqual('app', stack['role_group'])
        self.assertNotIn('role_arn', stack)
        self.assertEqual([], json.loads((ROOT / stack['parameters']).read_text()))


if __name__ == '__main__':
    unittest.main()
