"""AI 작업/결과 SQS 스택과 배포 IAM 권한 경계를 검증한다. AWS에 접근하지 않는다."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOB_QUEUE = 'Audigo-AI-Job-Queue'
RESULT_QUEUE = 'Audigo-AI-Result-Queue'


def actions(statement):
    value = statement['Action']
    return {value} if isinstance(value, str) else set(value)


def resources(statement):
    value = statement['Resource']
    values = value if isinstance(value, list) else [value]
    return {v['Fn::Sub'].rsplit(':', 1)[-1] if isinstance(v, dict) else v for v in values}


class SqsStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from cfnlint.decode import decode
        except ImportError:
            raise unittest.SkipTest('cfn-lint is installed in CI')
        cls.template, errors = decode(str(ROOT / 'cloudformation/sqs.yaml'))
        assert not errors, errors
        cls.iam, errors = decode(str(ROOT / 'bootstrap/infra-iam.yaml'))
        assert not errors, errors
        cls.config = json.loads((ROOT / 'deploy/stacks.json').read_text())
        cls.stack = next(s for s in cls.config['stacks'] if s['name'] == 'audigo-sqs')

    def queue(self, name):
        return next(r['Properties'] for r in self.template['Resources'].values()
                    if r['Type'] == 'AWS::SQS::Queue' and r['Properties']['QueueName'] == name)

    def test_queues_match_requested_standard_queue_settings(self):
        for name, visibility in ((JOB_QUEUE, 'JobQueueVisibilityTimeout'),
                                 (RESULT_QUEUE, 'ResultQueueVisibilityTimeout')):
            with self.subTest(queue=name):
                props = self.queue(name)
                self.assertNotIn('FifoQueue', props)
                self.assertEqual(20, props['ReceiveMessageWaitTimeSeconds'])
                self.assertEqual({'Ref': visibility}, props['VisibilityTimeout'])
                self.assertEqual(345600, props['MessageRetentionPeriod'])
                self.assertEqual(1048576, props['MaximumMessageSize'])
                self.assertIs(props['SqsManagedSseEnabled'], True)
        params = self.template['Parameters']
        self.assertEqual(360, params['JobQueueVisibilityTimeout']['Default'])
        self.assertEqual(60, params['ResultQueueVisibilityTimeout']['Default'])

    def test_no_dead_letter_queue_by_policy(self):
        # Job은 AI 내부 재시도, Result는 Backend 타임아웃으로 처리하므로 DLQ를 두지 않는다.
        queues = [r for r in self.template['Resources'].values() if r['Type'] == 'AWS::SQS::Queue']
        self.assertEqual({JOB_QUEUE, RESULT_QUEUE}, {q['Properties']['QueueName'] for q in queues})
        for queue in queues:
            self.assertNotIn('RedrivePolicy', queue['Properties'])
            self.assertNotIn('RedriveAllowPolicy', queue['Properties'])
        self.assertNotIn('EnableDeadLetterQueues', self.template['Parameters'])
        self.assertEqual({'HasAlarmTopic'}, set(self.template['Conditions']))

    def test_every_resource_is_retained_and_all_queue_names_fit_iam_scope(self):
        for logical_id, resource in self.template['Resources'].items():
            with self.subTest(resource=logical_id):
                self.assertEqual('Retain', resource['DeletionPolicy'])
                self.assertEqual('Retain', resource['UpdateReplacePolicy'])
                props = resource['Properties']
                if resource['Type'] == 'AWS::SQS::Queue':
                    self.assertTrue(props['QueueName'].startswith('Audigo-AI-'))
                elif resource['Type'] == 'AWS::CloudWatch::Alarm':
                    self.assertTrue(props['AlarmName'].startswith('audigo-sqs-'))
                else:
                    self.fail(f'예상하지 않은 리소스 유형: {resource["Type"]}')

    def test_requested_metrics_are_alarmed_on_both_queues(self):
        alarms = {(r['Properties']['Dimensions'][0]['Value']['Fn::GetAtt'][0], r['Properties']['MetricName'])
                  for r in self.template['Resources'].values()
                  if r['Type'] == 'AWS::CloudWatch::Alarm'}
        for queue in ('AiJobQueue', 'AiResultQueue'):
            for metric in ('ApproximateNumberOfMessagesVisible', 'ApproximateAgeOfOldestMessage',
                           'ApproximateNumberOfMessagesNotVisible'):
                self.assertIn((queue, metric), alarms)
        self.assertEqual(6, len(alarms))

    def test_stack_uses_platform_role_and_is_planned_by_deploy_role(self):
        self.assertEqual('platform', self.stack['role_group'])
        self.assertTrue(self.stack['enabled'])
        # 최초 생성 이후에는 수정만 허용하고, 스택이 사라져도 Queue를 다시 만들지 않는다.
        self.assertFalse(self.stack['allow_create'])
        deploy = self.iam['Resources']['AudigoGitHubInfraDeployRole']['Properties']['Policies'][0]
        sids = {s['Sid']: s for s in deploy['PolicyDocument']['Statement']}
        for sid in ('ReadApprovedAudigoStacks', 'PlanCreatesPlatformChangeSetWithPlatformExecutionRole',
                    'ProductionExecutesApprovedChangeSet'):
            self.assertIn('stack/audigo-sqs/*', json.dumps(sids[sid]))
        for sid in ('PlanCreatesCoreChangeSetWithCoreExecutionRole', 'PlanCreatesAppChangeSetWithAppExecutionRole'):
            self.assertNotIn('audigo-sqs', json.dumps(sids[sid]))

    def test_only_platform_manages_queues_and_cannot_touch_messages(self):
        def statements(role):
            return [s for p in self.iam['Resources'][role]['Properties']['Policies']
                    for s in p['PolicyDocument']['Statement']]
        for role in ('AudigoCfnCoreExecutionRole', 'AudigoCfnAppExecutionRole', 'AudigoGitHubInfraDeployRole'):
            found = set().union(*(actions(s) for s in statements(role)))
            self.assertFalse(any(a.startswith(('sqs:', 'cloudwatch:')) for a in found), role)
        platform = statements('AudigoCfnPlatformExecutionRole')
        sqs = [s for s in platform if any(a.startswith('sqs:') for a in actions(s))]
        self.assertEqual(1, len(sqs))
        self.assertEqual({'Audigo-AI-*'}, resources(sqs[0]))
        for forbidden in ('sqs:SendMessage', 'sqs:ReceiveMessage', 'sqs:DeleteMessage',
                          'sqs:PurgeQueue', 'sqs:DeleteQueue', 'sqs:SetQueueAttributes*'):
            self.assertNotIn(forbidden, actions(sqs[0]))
        for statement in platform:
            if any(a.startswith('cloudwatch:') for a in actions(statement)):
                if actions(statement) == {'cloudwatch:DescribeAlarms'}:
                    self.assertEqual({'Ref': 'AWS::Region'},
                                     statement['Condition']['StringEquals']['aws:RequestedRegion'])
                else:
                    self.assertTrue(statement['Resource']['Fn::Sub'].endswith(
                        '${TargetAccountId}:alarm:audigo-sqs-*'))
                    self.assertNotIn('cloudwatch:DeleteAlarms', actions(statement))


if __name__ == '__main__':
    unittest.main()
