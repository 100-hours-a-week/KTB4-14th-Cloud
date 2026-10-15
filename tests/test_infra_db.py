"""RDS 격리, 보존, 배포 권한 경계를 검증한다. AWS에 접근하지 않는다."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DbSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from cfnlint.decode import decode
        except ImportError:
            raise unittest.SkipTest('cfn-lint is installed in CI')
        cls.template, errors = decode(str(ROOT / 'cloudformation/db.yaml'))
        assert not errors, errors
        cls.iam, errors = decode(str(ROOT / 'bootstrap/infra-iam.yaml'))
        assert not errors, errors
        cls.config = json.loads((ROOT / 'deploy/stacks.json').read_text())
        cls.stack = next(s for s in cls.config['stacks'] if s['name'] == 'audigo-db')
        cls.parameters = {
            p['ParameterKey']: p['ParameterValue']
            for p in json.loads((ROOT / 'deploy/parameters/db.json').read_text())
        }

    def statements(self, role):
        return [s for policy in self.iam['Resources'][role]['Properties']['Policies']
                for s in policy['PolicyDocument']['Statement']]

    def actions(self, statement):
        value = statement['Action']
        return {value} if isinstance(value, str) else set(value)

    def test_stack_cannot_create_or_change_existing_network_or_application(self):
        resources = self.template['Resources']
        self.assertEqual({'AWS::RDS::DBInstance', 'AWS::RDS::DBSubnetGroup',
                          'AWS::RDS::DBParameterGroup'}, {r['Type'] for r in resources.values()})
        self.assertEqual(3, len(resources))
        self.assertEqual([{'Ref': 'ExistingPrivateSubnetAId'}, {'Ref': 'ExistingPrivateSubnetBId'}],
                         resources['DBSubnetGroup']['Properties']['SubnetIds'])
        self.assertEqual([{'Ref': 'ExistingDbSecurityGroupId'}],
                         resources['DBInstance']['Properties']['VPCSecurityGroups'])

    def test_private_database_and_data_survive_stack_deletion_or_replacement(self):
        for resource in self.template['Resources'].values():
            self.assertEqual('Retain', resource['DeletionPolicy'])
            self.assertEqual('Retain', resource['UpdateReplacePolicy'])
        props = self.template['Resources']['DBInstance']['Properties']
        self.assertIs(props['PubliclyAccessible'], False)
        self.assertIs(props['StorageEncrypted'], True)
        self.assertIs(props['DeletionProtection'], True)
        self.assertIs(props['DeleteAutomatedBackups'], False)
        self.assertNotIn('SourceDBInstanceIdentifier', props)
        self.assertNotIn('MasterUserPassword', props)
        self.assertIs(props['ManageMasterUserPassword'], True)
        self.assertEqual({'Fn::GetAtt': ['DBInstance', 'MasterUserSecret.SecretArn']},
                         self.template['Outputs']['MasterUserSecretArn']['Value'])

    def test_created_db_stack_stays_managed_without_recreation(self):
        # 최초 생성 이후에는 수정만 허용하고, 스택이 사라져도 DB를 다시 만들지 않는다.
        self.assertEqual('platform', self.stack['role_group'])
        self.assertTrue(self.stack['enabled'])
        self.assertFalse(self.stack['allow_create'])
        self.assertEqual([], self.stack['depends_on'])
        self.assertEqual('30', self.parameters['AllocatedStorage'])
        self.assertEqual('true', self.parameters['MultiAZ'])
        self.assertEqual('db.t4g.small', self.parameters['DBInstanceClass'])
        self.assertEqual('7', self.parameters['BackupRetentionPeriod'])
        self.assertEqual('UTC', self.parameters['DBTimeZone'])
        # RDS 기본값에 맡기지 않고 확인된 마이너 버전을 명시적으로 유지한다.
        self.assertNotIn('Default', self.template['Parameters']['DBEngineVersion'])
        self.assertEqual('8.4.11', self.parameters['DBEngineVersion'])

    def test_db_stack_only_uses_platform_role_for_planning(self):
        statements = self.statements('AudigoGitHubInfraDeployRole')
        for statement in statements:
            text = json.dumps(statement)
            if 'stack/audigo-db/' not in text:
                continue
            if 'cloudformation:CreateChangeSet' in self.actions(statement):
                self.assertEqual(
                    {'Fn::GetAtt': ['AudigoCfnPlatformExecutionRole', 'Arn']},
                    statement['Condition']['ArnEquals']['cloudformation:RoleArn'])
                self.assertEqual({'Ref': 'PlanOidcSubject'}, statement['Condition']['StringEquals']
                                 ['token.actions.githubusercontent.com:sub'])
            self.assertNotIn('cloudformation:DeleteStack', self.actions(statement))

    def test_only_platform_has_rds_and_secret_creation_permissions(self):
        for role in ['AudigoCfnCoreExecutionRole', 'AudigoCfnAppExecutionRole',
                     'AudigoGitHubInfraDeployRole']:
            actions = set().union(*(self.actions(s) for s in self.statements(role)))
            self.assertFalse(any(a.startswith(('rds:', 'secretsmanager:', 'kms:')) for a in actions))
        platform = self.statements('AudigoCfnPlatformExecutionRole')
        secrets = [s for s in platform if any(a.startswith('secretsmanager:') for a in self.actions(s))]
        self.assertEqual(1, len(secrets))
        self.assertEqual({'secretsmanager:CreateSecret', 'secretsmanager:TagResource'}, self.actions(secrets[0]))
        self.assertTrue(secrets[0]['Resource']['Fn::Sub'].endswith(':secret:rds!db-*'))
        for statement in platform:
            actions = self.actions(statement)
            if any(a.startswith('rds:') and not a.startswith('rds:Describe') for a in actions):
                self.assertNotEqual('*', statement['Resource'])
                resources = statement['Resource']
                if not isinstance(resources, list):
                    resources = [resources]
                for resource in resources:
                    self.assertTrue(resource['Fn::Sub'].endswith((
                        ':db:audigo-prd-db', ':pg:audigo-prd-db-parameters',
                        ':subgrp:audigo-prd-db-subnets', ':og:default:mysql-8-4')))
            self.assertNotIn('secretsmanager:GetSecretValue', actions)
            self.assertNotIn('rds:CreateDBInstanceReadReplica', actions)
            self.assertFalse(any(a.endswith(':*') for a in actions))

    def test_instance_create_can_use_only_the_intended_associated_groups(self):
        statement = next(s for s in self.statements('AudigoCfnPlatformExecutionRole')
                         if s['Sid'] == 'UseAudigoDbAssociatedGroups')
        self.assertEqual({'rds:CreateDBInstance', 'rds:ModifyDBInstance'}, self.actions(statement))
        self.assertEqual({':pg:audigo-prd-db-parameters', ':subgrp:audigo-prd-db-subnets',
                          ':og:default:mysql-8-4'},
                         {r['Fn::Sub'].split('${TargetAccountId}')[1] for r in statement['Resource']})

    def test_kms_grants_are_limited_to_rds_aws_managed_storage_key(self):
        statement = next(s for s in self.statements('AudigoCfnPlatformExecutionRole')
                         if 'kms:CreateGrant' in self.actions(s))
        condition = statement['Condition']
        self.assertEqual('true', condition['Bool']['kms:GrantIsForAWSResource'])
        self.assertEqual(['alias/aws/rds'], condition['ForAnyValue:StringEquals']['kms:ResourceAliases'])
        self.assertEqual({'Fn::Sub': 'rds.${AWS::Region}.amazonaws.com'},
                         condition['StringEquals']['kms:ViaService'])


if __name__ == '__main__':
    unittest.main()
