"""WebSocket Task Definition import boundaries; no AWS access."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'cloudformation/websocket.yaml'


class WebsocketImportTests(unittest.TestCase):
    def test_only_existing_task_definition_is_declared(self):
        text = TEMPLATE.read_text(encoding='utf-8')
        self.assertEqual(['AudigoWebsocketTaskDefinition'],
                         re.findall(r'^  (\w+):$', text, re.MULTILINE))
        self.assertEqual(['AWS::ECS::TaskDefinition'],
                         re.findall(r'^    Type: (\S+)$', text, re.MULTILINE))
        self.assertIn('    DeletionPolicy: Retain', text)
        self.assertIn('    UpdateReplacePolicy: Retain', text)
        self.assertNotIn('\nOutputs:', text)
        self.assertNotIn('\nParameters:', text)

    def test_existing_configuration_is_preserved(self):
        text = TEMPLATE.read_text(encoding='utf-8')
        for value in (
            'Family: audigo-websocket-Fargate', "Cpu: '512'", "Memory: '2048'",
            'CpuArchitecture: ARM64', 'OperatingSystemFamily: LINUX',
            'Name: spring-websocket', 'ContainerPort: 8081', 'HostPort: 8081',
            'Name: websocket-8080-tcp', 'MemoryReservation: 1536',
            '/ecs/audigo-websocket-Fargate', 'http://127.0.0.1:8081/actuator/health',
            'http://10.0.15.32:8000', 'Value: 10.0.7.252', 'Value: update',
            '@sha256:620387c453cedc0fdd05c4dd661c9a35bbd65a1e0644b70b7b3e91dfffedcf70',
            'ExecutionRoleArn: arn:aws:iam::185489190599:role/audigo-ecs-task-execution-role',
            'Name: DB_PASSWORD', 'backend-dTTCJ5:MYSQL_PASSWORD::',
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)
        self.assertNotIn('TaskRoleArn:', text)
        self.assertNotIn('ReadonlyRootFilesystem:', text)

    def test_app_role_group_and_no_parameters_are_added(self):
        config = json.loads((ROOT / 'deploy/stacks.json').read_text())
        stack = next(s for s in config['stacks'] if s['name'] == 'audigo-websocket')
        self.assertEqual('cloudformation/websocket.yaml', stack['template'])
        # CD 활성화 여부는 운영 설정이며 편입 안전성 계약과 별개입니다.
        self.assertFalse(stack['allow_create'])
        self.assertEqual('app', stack['role_group'])
        self.assertNotIn('role_arn', stack)
        self.assertEqual([], json.loads((ROOT / stack['parameters']).read_text()))

    def test_cfn_lint_and_parsed_properties_when_installed(self):
        executable = shutil.which('cfn-lint')
        if not executable:
            self.skipTest('cfn-lint unavailable locally; installed in GitHub CI')
        result = subprocess.run([executable, str(TEMPLATE)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        import yaml  # Installed transitively with cfn-lint in CI.
        props = yaml.safe_load(TEMPLATE.read_text())['Resources']['AudigoWebsocketTaskDefinition']['Properties']
        self.assertEqual(('512', '2048'), (props['Cpu'], props['Memory']))
        self.assertEqual(['FARGATE'], props['RequiresCompatibilities'])
        self.assertEqual('awsvpc', props['NetworkMode'])
        self.assertFalse(props['EnableFaultInjection'])
        self.assertEqual(1, len(props['ContainerDefinitions']))
        container = props['ContainerDefinitions'][0]
        self.assertEqual((512, 2048, 1536),
                         (container['Cpu'], container['Memory'], container['MemoryReservation']))
        env = {e['Name']: e['Value'] for e in container['Environment']}
        self.assertEqual(10, len(env))
        self.assertEqual('', env['KAKAO_CLIENT_SECRET'])
        self.assertEqual('3306', env['DB_PORT'])
        self.assertNotIn('DB_PASSWORD', env)
        self.assertEqual(7, len(container['Secrets']))
        self.assertTrue(all(s['ValueFrom'].startswith('arn:aws:secretsmanager:') for s in container['Secrets']))
        self.assertEqual({'Command': ['CMD-SHELL', 'curl -fsS http://127.0.0.1:8081/actuator/health >/dev/null'],
                          'Interval': 30, 'Timeout': 5, 'Retries': 5, 'StartPeriod': 120}, container['HealthCheck'])


if __name__ == '__main__':
    unittest.main()
