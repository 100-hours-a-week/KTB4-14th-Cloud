"""SQS 런타임 Task Role과 앱 CD PassRole의 권한 경계를 검증한다. AWS에 접근하지 않는다.

관리 경계: Task Role·권한은 IaC(bootstrap), Task Definition의 taskRoleArn·환경 변수는 앱 CD.
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'bootstrap/sqs-task-roles.yaml'
JOB_QUEUE = 'Audigo-AI-Job-Queue'
RESULT_QUEUE = 'Audigo-AI-Result-Queue'


def actions(statement):
    value = statement['Action']
    return {value} if isinstance(value, str) else set(value)


def resources(statement):
    value = statement['Resource']
    values = value if isinstance(value, list) else [value]
    return {v['Fn::Sub'].rsplit(':', 1)[-1] if isinstance(v, dict) and 'Fn::Sub' in v else json.dumps(v)
            for v in values}


class SqsTaskRoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from cfnlint.decode import decode
        except ImportError:
            raise unittest.SkipTest('cfn-lint is installed in CI')
        cls.template, errors = decode(str(TEMPLATE))
        assert not errors, errors
        cls.res = cls.template['Resources']

    def statements(self, logical_id):
        return self.res[logical_id]['Properties']['PolicyDocument']['Statement']

    def test_sqs_policies_follow_producer_consumer_direction(self):
        expected = {
            'BackendSqsPolicy': {
                'sqs:SendMessage': {JOB_QUEUE},
                'sqs:ReceiveMessage': {RESULT_QUEUE},
                'sqs:DeleteMessage': {RESULT_QUEUE},
                'sqs:ChangeMessageVisibility': {RESULT_QUEUE},
                'sqs:GetQueueAttributes': {JOB_QUEUE, RESULT_QUEUE},
            },
            'AiWorkerSqsPolicy': {
                'sqs:SendMessage': {RESULT_QUEUE},
                'sqs:ReceiveMessage': {JOB_QUEUE},
                'sqs:DeleteMessage': {JOB_QUEUE},
                'sqs:ChangeMessageVisibility': {JOB_QUEUE},
                'sqs:GetQueueAttributes': {JOB_QUEUE, RESULT_QUEUE},
            },
        }
        for logical_id, grants in expected.items():
            with self.subTest(policy=logical_id):
                actual = {}
                for statement in self.statements(logical_id):
                    self.assertEqual('Allow', statement['Effect'])
                    for action in actions(statement):
                        actual.setdefault(action, set()).update(resources(statement))
                self.assertEqual(grants, actual)

    def test_queue_names_match_sqs_stack(self):
        sqs = (ROOT / 'cloudformation/sqs.yaml').read_text(encoding='utf-8')
        for name in (JOB_QUEUE, RESULT_QUEUE):
            self.assertIn(f'QueueName: {name}', sqs)

    def test_new_task_roles_trust_only_ecs_tasks_in_this_account(self):
        for role in ('BackendTaskRole', 'AiWorkerTaskRole'):
            with self.subTest(role=role):
                props = self.res[role]['Properties']
                trust = props['AssumeRolePolicyDocument']['Statement'][0]
                self.assertEqual({'Service': 'ecs-tasks.amazonaws.com'}, trust['Principal'])
                self.assertIn('aws:SourceAccount', trust['Condition']['StringEquals'])
                self.assertNotIn('Policies', props)
                self.assertNotIn('ManagedPolicyArns', props)

    def test_existing_task_roles_can_be_reused(self):
        self.assertEqual('CreateBackendTaskRole', self.res['BackendTaskRole']['Condition'])
        self.assertEqual('CreateAiWorkerTaskRole', self.res['AiWorkerTaskRole']['Condition'])
        for policy, condition, existing in (
            ('BackendSqsPolicy', 'CreateBackendTaskRole', 'ExistingBackendTaskRoleName'),
            ('AiWorkerSqsPolicy', 'CreateAiWorkerTaskRole', 'ExistingAiWorkerTaskRoleName'),
        ):
            roles = self.res[policy]['Properties']['Roles'][0]['Fn::If']
            self.assertEqual(condition, roles[0])
            self.assertEqual({'Ref': existing}, roles[2])

    def test_ai_drain_runtime_permissions_only_on_new_ai_role(self):
        policy = self.res['AiWorkerRuntimePolicy']
        self.assertEqual('CreateAiWorkerTaskRole', policy['Condition'])
        self.assertEqual([{'Ref': 'AiWorkerTaskRole'}], policy['Properties']['Roles'])
        grants = {a: s for s in self.statements('AiWorkerRuntimePolicy') for a in actions(s)}
        self.assertEqual({'ecs:GetTaskProtection', 'ecs:UpdateTaskProtection', 'ssm:GetParameter'}, set(grants))
        self.assertTrue(grants['ecs:UpdateTaskProtection']['Resource']['Fn::Sub'].endswith(':task/${EcsClusterName}/*'))
        self.assertTrue(grants['ssm:GetParameter']['Resource']['Fn::Sub'].endswith(':parameter${AiActiveVersionParameterName}'))

    def test_app_cd_can_pass_only_its_own_task_role_to_ecs_tasks(self):
        for policy, condition, deploy_role, task_role in (
            ('BackendDeployPassTaskRolePolicy', 'HasBackendDeployRole', 'BackendDeployRoleName', 'BackendTaskRole'),
            ('AiDeployPassTaskRolePolicy', 'HasAiDeployRole', 'AiDeployRoleName', 'AiWorkerTaskRole'),
        ):
            with self.subTest(policy=policy):
                resource = self.res[policy]
                self.assertEqual(condition, resource['Condition'])
                self.assertEqual([{'Ref': deploy_role}], resource['Properties']['Roles'])
                (statement,) = self.statements(policy)
                self.assertEqual({'iam:PassRole'}, actions(statement))
                self.assertEqual('ecs-tasks.amazonaws.com',
                                 statement['Condition']['StringEquals']['iam:PassedToService'])
                self.assertEqual({'Fn::GetAtt': [task_role, 'Arn']}, statement['Resource']['Fn::If'][1])

    def test_no_wildcard_actions_or_queue_management(self):
        for logical_id, resource in self.res.items():
            if resource['Type'] != 'AWS::IAM::ManagedPolicy':
                continue
            for statement in self.statements(logical_id):
                for action in actions(statement):
                    self.assertNotIn('*', action, logical_id)
                    self.assertNotIn(action, {'sqs:CreateQueue', 'sqs:DeleteQueue', 'sqs:PurgeQueue',
                                              'sqs:SetQueueAttributes'}, logical_id)
                self.assertNotEqual('*', statement['Resource'], logical_id)

    def test_every_resource_is_retained(self):
        for logical_id, resource in self.res.items():
            with self.subTest(resource=logical_id):
                self.assertEqual('Retain', resource['DeletionPolicy'])
                self.assertEqual('Retain', resource['UpdateReplacePolicy'])

    def test_task_roles_template_is_bootstrap_only_and_linted_in_ci(self):
        config = (ROOT / 'deploy/stacks.json').read_text(encoding='utf-8')
        self.assertNotIn('sqs-task-roles', config)
        ci = (ROOT / '.github/workflows/cloudformation-ci.yml').read_text(encoding='utf-8')
        self.assertIn('bootstrap/sqs-task-roles.yaml', ci)


if __name__ == '__main__':
    unittest.main()
