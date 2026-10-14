"""Structural security checks for the separately deployed IAM bootstrap template."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "bootstrap" / "infra-iam.yaml"


class IamBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = TEMPLATE.read_text(encoding="utf-8")

    def test_defines_exactly_four_named_deployment_roles(self):
        for role in (
            "AudigoGitHubInfraDeployRole",
            "AudigoCfnCoreExecutionRole",
            "AudigoCfnPlatformExecutionRole",
            "AudigoCfnAppExecutionRole",
        ):
            self.assertIn(f"RoleName: {role}", self.text)
        self.assertEqual(4, self.text.count("Type: AWS::IAM::Role"))

    def test_execution_roles_trust_only_cloudformation(self):
        for logical_id in (
            "AudigoCfnCoreExecutionRole",
            "AudigoCfnPlatformExecutionRole",
            "AudigoCfnAppExecutionRole",
        ):
            start = self.text.index(f"  {logical_id}:")
            end = self.text.find("\n  Audigo", start + 1)
            block = self.text[start : None if end == -1 else end]
            self.assertIn("Service: cloudformation.amazonaws.com", block)
            self.assertNotIn("Federated:", block)

    def test_github_trust_uses_actual_subject_parameters_not_guessed_repository_text(self):
        start = self.text.index("  AudigoGitHubInfraDeployRole:")
        end = self.text.index("\n  AudigoCfnCoreExecutionRole:", start)
        block = self.text[start:end]
        self.assertIn("Federated: !Ref GitHubOidcProviderArn", block)
        self.assertIn("token.actions.githubusercontent.com:aud: sts.amazonaws.com", block)
        self.assertIn("- !Ref PlanOidcSubject", block)
        self.assertIn("- !Ref ProductionOidcSubject", block)
        self.assertIn("token\\.actions\\.githubusercontent\\.com", self.text)
        self.assertIn("OidcSubjectsMustDiffer", self.text)
        self.assertIn("environment:iac-plan", self.text)
        self.assertIn("environment:iac-production", self.text)

    def test_plan_and_production_permissions_are_separated(self):
        plan = self.text[self.text.index("Sid: PlanCreatesCoreChangeSetWithCoreExecutionRole") : self.text.index("Sid: ProductionExecutesApprovedChangeSet")]
        production = self.text[self.text.index("Sid: ProductionExecutesApprovedChangeSet") : self.text.index("Sid: PassOnlyApprovedExecutionRolesToCloudFormation")]
        self.assertIn("cloudformation:CreateChangeSet", plan)
        self.assertIn("token.actions.githubusercontent.com:sub: !Ref PlanOidcSubject", plan)
        self.assertNotIn("cloudformation:ExecuteChangeSet", plan)
        self.assertIn("cloudformation:ExecuteChangeSet", production)
        self.assertIn("token.actions.githubusercontent.com:sub: !Ref ProductionOidcSubject", production)
        self.assertNotIn("cloudformation:CreateChangeSet", production)

    def test_create_change_set_cannot_cross_stack_group_and_execution_role(self):
        groups = {
            "Core": ("audigo-network", "audigo-dns", "AudigoCfnCoreExecutionRole.Arn"),
            "Platform": ("audigo-ecs-common", "audigo-alb", "AudigoCfnPlatformExecutionRole.Arn"),
            "App": ("audigo-rest-api", "audigo-websocket", "audigo-frontend", "AudigoCfnAppExecutionRole.Arn"),
        }
        for group, expected in groups.items():
            start = self.text.index(f"Sid: PlanCreates{group}ChangeSetWith{group}ExecutionRole")
            end = self.text.find("\n              - Sid:", start + 1)
            block = self.text[start : None if end == -1 else end]
            for value in expected:
                self.assertIn(value, block)
            for other_group, other_expected in groups.items():
                if other_group != group:
                    self.assertNotIn(other_expected[-1], block)

        pass_role = self.text[self.text.index("Sid: PassOnlyApprovedExecutionRolesToCloudFormation") : self.text.index("\n  AudigoCfnCoreExecutionRole:")]
        self.assertIn("token.actions.githubusercontent.com:sub: !Ref PlanOidcSubject", pass_role)
        self.assertNotIn("ProductionOidcSubject", pass_role)

    def test_no_administrator_service_wildcard_or_deferred_service_permissions(self):
        self.assertNotIn("AdministratorAccess", self.text)
        self.assertNotIn("PowerUserAccess", self.text)
        self.assertIsNone(re.search(r"^\s*-\s*[a-z0-9-]+:\*$", self.text, re.MULTILINE))
        for forbidden in (
            "ecr:",
            "cloudwatch:",
            "logs:",
            "opensearch:",
            "aps:",
            "grafana:",
            "secretsmanager:",
            "rds:",
            "elasticache:",
            "sqs:",
        ):
            self.assertNotIn(forbidden, self.text)

    def test_action_names_and_ecs_reads_are_resource_scoped(self):
        self.assertIn("s3:GetEncryptionConfiguration", self.text)
        self.assertIn("s3:PutEncryptionConfiguration", self.text)
        self.assertNotIn("s3:GetBucketEncryption", self.text)
        self.assertNotIn("s3:PutBucketEncryption", self.text)
        self.assertIn("ecs:UpdateCluster", self.text)
        for sid in ("ReadManagedEcsClusters", "ReadManagedRestService", "ReadManagedTaskDefinitions"):
            start = self.text.index(f"Sid: {sid}")
            end = self.text.find("\n              - Sid:", start + 1)
            block = self.text[start : None if end == -1 else end]
            self.assertNotIn("Resource: '*'", block)

    def test_unscopable_describe_actions_use_region_limited_wildcard(self):
        for sid, action in (
            ("DescribeTaskDefinitionsInDeploymentRegion", "ecs:DescribeTaskDefinition"),
            ("ReadApplicationAutoScalingState", "application-autoscaling:DescribeScalableTargets"),
        ):
            start = self.text.index(f"Sid: {sid}")
            end = self.text.find("\n              - Sid:", start + 1)
            block = self.text[start:end]
            self.assertIn(action, block)
            self.assertIn("Resource: '*'", block)
            self.assertIn("aws:RequestedRegion: !Ref AWS::Region", block)

    def test_execution_roles_cannot_manage_iam_or_bootstrap_roles(self):
        execution = self.text[self.text.index("  AudigoCfnCoreExecutionRole:") : self.text.index("\nOutputs:")]
        for forbidden in (
            "iam:CreateRole",
            "iam:UpdateRole",
            "iam:DeleteRole",
            "iam:AttachRolePolicy",
            "iam:PutRolePolicy",
            "iam:CreateOpenIDConnectProvider",
            "iam:UpdateOpenIDConnectProvider",
        ):
            self.assertNotIn(forbidden, execution)
        self.assertNotIn("AudigoCfnCoreExecutionRole.Arn", execution)
        self.assertNotIn("AudigoCfnPlatformExecutionRole.Arn", execution)
        self.assertNotIn("AudigoCfnAppExecutionRole.Arn", execution)

    def test_bootstrap_is_not_a_normal_deployment_template(self):
        config = (ROOT / "deploy" / "stacks.json").read_text(encoding="utf-8")
        self.assertNotIn("bootstrap/infra-iam.yaml", config)
        self.assertNotIn("audigo-infra-iam", config)

    def test_pass_role_combines_both_conditions_without_duplicate_operator(self):
        start = self.text.index("Sid: PassOnlyApprovedExecutionRolesToCloudFormation")
        end = self.text.index("\n  AudigoCfnCoreExecutionRole:", start)
        block = self.text[start:end]
        self.assertEqual(1, block.count("StringEquals:"))
        self.assertIn("iam:PassedToService: cloudformation.amazonaws.com", block)
        self.assertIn("token.actions.githubusercontent.com:sub: !Ref PlanOidcSubject", block)


if __name__ == "__main__":
    unittest.main()
