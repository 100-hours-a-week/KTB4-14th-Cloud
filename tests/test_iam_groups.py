"""Contract tests for CloudFormation execution-role group selection."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("infra_iam_groups", ROOT / "scripts" / "infra.py")
infra = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(infra)


class IamGroupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "deploy").mkdir()
        (self.root / "cloudformation").mkdir()
        (self.root / "parameters").mkdir()
        (self.root / "cloudformation/network.yaml").write_text("Resources: {}\n", encoding="utf-8")
        (self.root / "parameters/network.json").write_text("[]\n", encoding="utf-8")
        self.config_path = self.root / "deploy/stacks.json"
        self.write_config()

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self, stack_changes=None, execution_roles=None):
        stack = {
            "name": "audigo-network",
            "template": "cloudformation/network.yaml",
            "parameters": "parameters/network.json",
            "role_group": "core",
            "enabled": True,
            "allow_create": False,
            "depends_on": [],
        }
        stack.update(stack_changes or {})
        roles = execution_roles or {
            "core": "${INFRA_CORE_CFN_ROLE_ARN}",
            "platform": "${INFRA_PLATFORM_CFN_ROLE_ARN}",
            "app": "${INFRA_APP_CFN_ROLE_ARN}",
        }
        self.config_path.write_text(
            json.dumps({"region": "ap-northeast-2", "execution_roles": roles, "stacks": [stack]}),
            encoding="utf-8",
        )

    def test_active_stack_resolves_only_its_group_role(self):
        data, stacks, _ = infra.validate_config(self.config_path)
        with patch.dict(os.environ, {"INFRA_CORE_CFN_ROLE_ARN": "arn:aws:iam::1:role/core"}, clear=True):
            self.assertEqual("arn:aws:iam::1:role/core", infra.resolve_stack_role(data, stacks[0]))

    def test_active_stack_fails_closed_when_group_role_environment_is_missing(self):
        data, stacks, _ = infra.validate_config(self.config_path)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(infra.InfraError, "INFRA_CORE_CFN_ROLE_ARN"):
                infra.resolve_stack_role(data, stacks[0])

    def test_unknown_or_missing_active_role_group_is_rejected(self):
        self.write_config(stack_changes={"role_group": "other"})
        with self.assertRaisesRegex(infra.InfraError, "알 수 없는 role_group"):
            infra.validate_config(self.config_path)
        self.write_config(stack_changes={"role_group": ["core"]})
        with self.assertRaisesRegex(infra.InfraError, "알 수 없는 role_group"):
            infra.validate_config(self.config_path)
        self.write_config(stack_changes={"role_group": None})
        with self.assertRaisesRegex(infra.InfraError, "role_group이 필요"):
            infra.validate_config(self.config_path)

    def test_legacy_per_stack_role_arn_is_rejected(self):
        self.write_config(stack_changes={"role_arn": "arn:aws:iam::1:role/unreviewed"})
        with self.assertRaisesRegex(infra.InfraError, "필수 키"):
            infra.validate_config(self.config_path)

    def test_execution_role_map_must_define_exactly_three_approved_groups(self):
        self.write_config(execution_roles={"core": "role"})
        with self.assertRaisesRegex(infra.InfraError, "execution_roles"):
            infra.validate_config(self.config_path)

    def test_apply_report_cannot_change_the_approved_role_group(self):
        report = self.root / "report.json"
        report.write_text(
            json.dumps({
                "version": 1,
                "commit": "a" * 40,
                "region": "ap-northeast-2",
                "stacks": [{
                    "name": "audigo-network",
                    "change_set_arn": "arn:change",
                    "change_type": "UPDATE",
                    "status": "READY",
                    "role_group": "app",
                    "role_arn": "arn:aws:iam::1:role/core",
                    "marker": "audigo-iac commit=" + "a" * 40 + " type=UPDATE role=arn:aws:iam::1:role/core",
                }],
            }),
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"INFRA_CORE_CFN_ROLE_ARN": "arn:aws:iam::1:role/core"}, clear=True):
            with self.assertRaisesRegex(infra.InfraError, "역할 또는 상태"):
                infra.apply_one(self.config_path, report, "audigo-network", "a" * 40)

    def test_repository_mapping_and_single_caller_workflow(self):
        config = json.loads((ROOT / "deploy/stacks.json").read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "core": "${INFRA_CORE_CFN_ROLE_ARN}",
                "platform": "${INFRA_PLATFORM_CFN_ROLE_ARN}",
                "app": "${INFRA_APP_CFN_ROLE_ARN}",
            },
            config["execution_roles"],
        )
        groups = {stack["name"]: stack.get("role_group") for stack in config["stacks"]}
        self.assertEqual("core", groups["audigo-network"])
        self.assertEqual("platform", groups["audigo-alb"])
        self.assertEqual("platform", groups["audigo-ecs-common"])
        self.assertEqual("app", groups["audigo-frontend"])
        self.assertEqual("app", groups["audigo-rest-api"])
        self.assertEqual("app", groups["audigo-websocket"])
        for name in ("audigo-db", "audigo-cache", "audigo-sqs", "audigo-ai-api", "audigo-monitoring"):
            self.assertIsNone(groups[name])

        workflow = (ROOT / ".github/workflows/cloudformation-cd.yml").read_text(encoding="utf-8")
        self.assertEqual(2, workflow.count("role-to-assume: ${{ vars.INFRA_DEPLOY_ROLE_ARN }}"))
        self.assertNotIn("INFRA_PLAN_ROLE_ARN", workflow)
        self.assertNotIn("INFRA_APPLY_ROLE_ARN", workflow)
        for variable in ("INFRA_CORE_CFN_ROLE_ARN", "INFRA_PLATFORM_CFN_ROLE_ARN", "INFRA_APP_CFN_ROLE_ARN"):
            self.assertIn(variable, workflow)
        self.assertIn("변경 파일 자동 감지는 하지 않으며", workflow)


if __name__ == "__main__":
    unittest.main()
