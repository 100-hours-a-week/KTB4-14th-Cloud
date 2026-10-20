"""세션용 ElastiCache Serverless(Valkey) 스택과 배포·런타임 IAM 권한 경계를 검증한다. AWS에 접근하지 않는다."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_NAME = 'audigo-session'
APP_USER = 'audigo'


def actions(statement):
    value = statement['Action']
    return {value} if isinstance(value, str) else set(value)


class CacheStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from cfnlint.decode import decode
        except ImportError:
            raise unittest.SkipTest('cfn-lint is installed in CI')
        cls.template, errors = decode(str(ROOT / 'cloudformation/cache.yaml'))
        assert not errors, errors
        cls.iam, errors = decode(str(ROOT / 'bootstrap/infra-iam.yaml'))
        assert not errors, errors
        cls.config = json.loads((ROOT / 'deploy/stacks.json').read_text())
        cls.stack = next(s for s in cls.config['stacks'] if s['name'] == 'audigo-cache')
        cls.resources = cls.template['Resources']

    def of_type(self, type_name):
        return [r['Properties'] for r in self.resources.values() if r['Type'] == type_name]

    def test_serverless_valkey_with_requested_limits_and_no_snapshot(self):
        [cache] = self.of_type('AWS::ElastiCache::ServerlessCache')
        self.assertEqual(CACHE_NAME, cache['ServerlessCacheName'])
        self.assertEqual('valkey', cache['Engine'])
        self.assertEqual({'Maximum': {'Ref': 'MaxDataStorageGB'}, 'Unit': 'GB'}, cache['CacheUsageLimits']['DataStorage'])
        self.assertEqual(1, self.template['Parameters']['MaxDataStorageGB']['Default'])
        self.assertEqual(0, cache['SnapshotRetentionLimit'])
        self.assertNotIn('DailySnapshotTime', cache)
        self.assertEqual(2, len(cache['SubnetIds']))
        self.assertEqual([{'Ref': 'CacheSecurityGroup'}], cache['SecurityGroupIds'])
        self.assertEqual({'Ref': 'SessionUserGroup'}, cache['UserGroupId'])

    def test_security_group_only_allows_backend_on_valkey_ports(self):
        [sg] = self.of_type('AWS::EC2::SecurityGroup')
        sources = set()
        for ingress in sg['SecurityGroupIngress']:
            self.assertEqual(('tcp', 6379, 6380), (ingress['IpProtocol'], ingress['FromPort'], ingress['ToPort']))
            self.assertNotIn('CidrIp', ingress)
            sources.add(ingress['SourceSecurityGroupId']['Ref'])
        self.assertEqual({'AppSecurityGroupId', 'SecondaryAppSecurityGroupId'}, sources)
        [egress] = sg['SecurityGroupEgress']
        self.assertEqual('127.0.0.1/32', egress['CidrIp'])

    def test_password_user_lives_in_admin_stack_and_valkey_group_has_no_default_user(self):
        # 비밀번호 사용자는 CD 스택에 두지 않는다. Valkey는 no-password-required 사용자를 허용하지 않고,
        # Valkey 사용자 그룹은 default 사용자 없이 무인증 연결을 막으므로 사용자를 정의하지 않는다.
        self.assertEqual([], self.of_type('AWS::ElastiCache::User'))
        [group] = self.of_type('AWS::ElastiCache::UserGroup')
        self.assertEqual('valkey', group['Engine'])
        self.assertEqual('audigo-session-users', group['UserGroupId'])
        self.assertEqual([APP_USER], group['UserIds'])

        # 비밀번호 사용자와 Secret은 IaC 어디에도 정의하지 않는다(관리자가 audigo/v1/backend에서 관리).
        for path in ('cloudformation/cache.yaml', 'bootstrap/infra-iam.yaml'):
            text = (ROOT / path).read_text()
            self.assertNotIn('{{resolve:secretsmanager', text)
            self.assertNotIn('AWS::SecretsManager::Secret', text)

    def test_every_resource_is_retained(self):
        for logical_id, resource in self.resources.items():
            with self.subTest(resource=logical_id):
                self.assertEqual('Retain', resource['DeletionPolicy'])
                self.assertEqual('Retain', resource['UpdateReplacePolicy'])

    def test_stack_uses_platform_role_and_is_planned_by_deploy_role(self):
        self.assertEqual('platform', self.stack['role_group'])
        self.assertTrue(self.stack['enabled'])
        # 최초 생성 PR에서만 true. CREATE_COMPLETE 확인 후 별도 PR로 false로 되돌린다.
        self.assertTrue(self.stack['allow_create'])
        deploy = self.iam['Resources']['AudigoGitHubInfraDeployRole']['Properties']['Policies'][0]
        sids = {s['Sid']: s for s in deploy['PolicyDocument']['Statement']}
        for sid in ('ReadApprovedAudigoStacks', 'PlanCreatesPlatformChangeSetWithPlatformExecutionRole',
                    'ProductionExecutesApprovedChangeSet'):
            self.assertIn('stack/audigo-cache/*', json.dumps(sids[sid]))
        for sid in ('PlanCreatesCoreChangeSetWithCoreExecutionRole', 'PlanCreatesAppChangeSetWithAppExecutionRole'):
            self.assertNotIn('audigo-cache', json.dumps(sids[sid]))

    def test_only_platform_manages_cache_and_cannot_delete_or_connect(self):
        def statements(role):
            return [s for p in self.iam['Resources'][role]['Properties']['Policies']
                    for s in p['PolicyDocument']['Statement']]
        for role in ('AudigoCfnCoreExecutionRole', 'AudigoCfnAppExecutionRole', 'AudigoGitHubInfraDeployRole'):
            found = set().union(*(actions(s) for s in statements(role)))
            self.assertFalse(any(a.startswith('elasticache:') for a in found), role)
        platform = set().union(*(actions(s) for s in statements('AudigoCfnPlatformExecutionRole')))
        for forbidden in ('elasticache:Connect', 'elasticache:DeleteServerlessCache', 'elasticache:DeleteUser',
                          'elasticache:DeleteUserGroup', 'ec2:DeleteSecurityGroup', 'secretsmanager:GetSecretValue'):
            self.assertNotIn(forbidden, platform)
        # Platform은 앱 사용자 비밀번호를 바꿀 수 없다(CreateUser/ModifyUser는 비활성 default 사용자만).
        for statement in statements('AudigoCfnPlatformExecutionRole'):
            if actions(statement) & {'elasticache:CreateUser', 'elasticache:ModifyUser'}:
                self.assertTrue(statement['Resource']['Fn::Sub'].endswith(':user:audigo-default-off'))
        cache_stmt = next(s for s in statements('AudigoCfnPlatformExecutionRole') if s['Sid'] == 'ManageAudigoSessionCache')
        arns = json.dumps(cache_stmt['Resource'])
        self.assertIn(f'serverlesscache:{CACHE_NAME}', arns)
        self.assertNotIn('serverlesscache:*', arns)


if __name__ == '__main__':
    unittest.main()
