import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE = Path(__file__).parents[1] / "scripts" / "infra.py"
SPEC = importlib.util.spec_from_file_location("infra", MODULE)
infra = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(infra)


SHA = "a" * 40
ROLE = "arn:aws:iam::1:role/network"
MARKER = f"audigo-iac commit={SHA} type=UPDATE role={ROLE}"


class InfraTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "deploy").mkdir()
        (self.root / "cloudformation").mkdir()
        (self.root / "parameters").mkdir()
        (self.root / "cloudformation/network.yaml").write_text("Resources: {}\n")
        (self.root / "parameters/network.json").write_text("[]\n")
        self.config_path = self.root / "deploy/stacks.json"
        self.write_config()

    def tearDown(self):
        self.tmp.cleanup()

    def write_config(self, **changes):
        stack = {"name": "audigo-network", "template": "cloudformation/network.yaml", "parameters": "parameters/network.json", "role_arn": "${NETWORK_CFN_ROLE_ARN}", "enabled": True, "allow_create": False, "depends_on": []}
        stack.update(changes)
        self.config_path.write_text(json.dumps({"region": "ap-northeast-2", "stacks": [stack]}))

    def test_validate_rejects_enabled_empty_template(self):
        (self.root / "cloudformation/network.yaml").write_text("")
        with self.assertRaisesRegex(infra.InfraError, "활성화"):
            infra.validate_config(self.config_path)

    def test_validate_allows_disabled_empty_template_and_reports_waiting(self):
        (self.root / "cloudformation/network.yaml").write_text("")
        self.write_config(enabled=False)
        _, _, messages = infra.validate_config(self.config_path)
        self.assertIn("WAITING audigo-network", messages[0])

    def test_validate_rejects_unregistered_template_and_cycle(self):
        (self.root / "cloudformation/extra.yaml").write_text("Resources: {}\n")
        with self.assertRaisesRegex(infra.InfraError, "등록되지 않은"):
            infra.validate_config(self.config_path)
        (self.root / "cloudformation/extra.yaml").unlink()
        self.write_config(depends_on=["audigo-network"])
        with self.assertRaisesRegex(infra.InfraError, "자기 자신"):
            infra.validate_config(self.config_path)

    def test_validate_rejects_invalid_parameters(self):
        (self.root / "parameters/network.json").write_text("{}")
        with self.assertRaisesRegex(infra.InfraError, "JSON 배열"):
            infra.validate_config(self.config_path)
        (self.root / "parameters/network.json").write_text('[{"ParameterKey":"VpcId","Unknown":"x"}]')
        with self.assertRaisesRegex(infra.InfraError, "허용되지 않은"):
            infra.validate_config(self.config_path)

    def test_plan_rejects_missing_stack_without_create_permission(self):
        with patch.object(infra, "aws", side_effect=infra.InfraError("ValidationError: Stack with id x does not exist")):
            with patch.dict(os.environ, {"NETWORK_CFN_ROLE_ARN": "arn:aws:iam::1:role/network"}):
                with self.assertRaisesRegex(infra.InfraError, "생성은 허용되지"):
                    infra.plan_one(self.config_path, "audigo-network", SHA)

    def test_access_denied_is_not_treated_as_missing_stack(self):
        with patch.object(infra, "aws", side_effect=infra.InfraError("AccessDenied: denied")):
            with patch.dict(os.environ, {"NETWORK_CFN_ROLE_ARN": "arn:aws:iam::1:role/network"}):
                with self.assertRaisesRegex(infra.InfraError, "AccessDenied"):
                    infra.plan_one(self.config_path, "audigo-network", SHA)

    def test_plan_accepts_only_genuine_update_no_change(self):
        calls = []
        def fake_aws(args):
            calls.append(args)
            if args[1] == "describe-stacks": return {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]}
            if args[1] == "create-change-set": return {"Id": "arn:change"}
            if args[1] == "wait": raise infra.InfraError("waiter failed")
            if args[1] == "describe-change-set": return {"StackName": "audigo-network", "ChangeSetId": "arn:change", "Description": MARKER, "Status": "FAILED", "StatusReason": "No updates are to be performed."}
            raise AssertionError(args)
        with patch.object(infra, "aws", side_effect=fake_aws), patch.dict(os.environ, {"NETWORK_CFN_ROLE_ARN": "arn:aws:iam::1:role/network"}):
            result = infra.plan_one(self.config_path, "audigo-network", SHA)
        self.assertEqual("NO_CHANGES", result["status"])
        self.assertTrue(any(c[1] == "wait" for c in calls))

    def test_plan_blocks_remove_and_replacement(self):
        def fake_aws(args):
            if args[1] == "describe-stacks": return {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]}
            if args[1] == "create-change-set": return {"Id": "arn:change"}
            if args[1] == "wait": return {}
            return {"StackName": "audigo-network", "ChangeSetId": "arn:change", "Description": MARKER, "Status": "CREATE_COMPLETE", "Changes": [{"ResourceChange": {"LogicalResourceId": "Vpc", "Action": "Modify", "Replacement": "Conditional"}}]}
        with patch.object(infra, "aws", side_effect=fake_aws), patch.dict(os.environ, {"NETWORK_CFN_ROLE_ARN": "arn:aws:iam::1:role/network"}):
            with self.assertRaisesRegex(infra.InfraError, "삭제 또는 교체"):
                infra.plan_one(self.config_path, "audigo-network", SHA)

    def test_apply_rechecks_guard_before_execute(self):
        report = self.root / "report.json"
        report.write_text(json.dumps({"version": 1, "commit": SHA, "region": "ap-northeast-2", "stacks": [{"name": "audigo-network", "change_set_arn": "arn:change", "change_type": "UPDATE", "status": "READY", "role_arn": ROLE, "marker": MARKER}]}))
        def fake_aws(args):
            if args[1] == "describe-change-set": return {"StackName": "audigo-network", "ChangeSetId": "arn:change", "Description": MARKER, "Status": "CREATE_COMPLETE", "ExecutionStatus": "AVAILABLE", "Changes": [{"ResourceChange": {"LogicalResourceId": "Gone", "Action": "Remove", "Replacement": "False"}}]}
            raise AssertionError("execute must not be called")
        with patch.object(infra, "aws", side_effect=fake_aws), patch.dict(os.environ, {"NETWORK_CFN_ROLE_ARN": "arn:aws:iam::1:role/network"}):
            with self.assertRaisesRegex(infra.InfraError, "삭제 또는 교체"):
                infra.apply_one(self.config_path, report, "audigo-network", SHA)

    def test_plan_allows_stable_dependency(self):
        dependent = {"name": "audigo-db", "template": "cloudformation/db.yaml", "parameters": "parameters/db.json", "role_arn": "${NETWORK_CFN_ROLE_ARN}", "enabled": True, "allow_create": False, "depends_on": ["audigo-network"]}
        (self.root / "cloudformation/db.yaml").write_text("Resources: {}\n")
        (self.root / "parameters/db.json").write_text("[]")
        config = json.loads(self.config_path.read_text())
        config["stacks"].append(dependent)
        self.config_path.write_text(json.dumps(config))
        def fake_aws(args):
            if args[1] == "describe-stacks": return {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]}
            if args[1] == "create-change-set": return {"Id": "arn:db"}
            if args[1] == "wait": return {}
            return {"StackName": "audigo-db", "ChangeSetId": "arn:db", "Description": f"audigo-iac commit={SHA} type=UPDATE role={ROLE}", "Status": "CREATE_COMPLETE", "Changes": []}
        with patch.object(infra, "aws", side_effect=fake_aws), patch.dict(os.environ, {"NETWORK_CFN_ROLE_ARN": "arn:aws:iam::1:role/network"}):
            result = infra.plan_one(self.config_path, "audigo-db", SHA)
        self.assertEqual("READY", result["status"])

    def test_plan_rejects_stack_update_in_progress(self):
        with patch.object(infra, "aws", return_value={"Stacks": [{"StackStatus": "UPDATE_IN_PROGRESS"}]}), patch.dict(os.environ, {"NETWORK_CFN_ROLE_ARN": "arn:aws:iam::1:role/network"}):
            with self.assertRaisesRegex(infra.InfraError, "안정 상태"):
                infra.plan_one(self.config_path, "audigo-network", SHA)

    def test_apply_rejects_tampered_create_when_create_disabled(self):
        report = self.root / "report.json"
        report.write_text(json.dumps({"version": 1, "commit": SHA, "region": "ap-northeast-2", "stacks": [{"name": "audigo-network", "change_set_arn": "arn:change", "change_type": "CREATE", "status": "READY", "role_arn": ROLE, "marker": "audigo-iac commit=" + SHA + " type=CREATE role=" + ROLE}]}))
        with patch.dict(os.environ, {"NETWORK_CFN_ROLE_ARN": "arn:aws:iam::1:role/network"}):
            with self.assertRaisesRegex(infra.InfraError, "CREATE"):
                infra.apply_one(self.config_path, report, "audigo-network", SHA)

    def test_apply_rejects_change_set_for_another_stack(self):
        report = self.root / "report.json"
        report.write_text(json.dumps({"version": 1, "commit": SHA, "region": "ap-northeast-2", "stacks": [{"name": "audigo-network", "change_set_arn": "arn:change", "change_type": "UPDATE", "status": "READY", "role_arn": ROLE, "marker": MARKER}]}))
        def fake_aws(args):
            return {"StackName": "other-stack", "ChangeSetId": "arn:change", "Description": MARKER, "Status": "CREATE_COMPLETE", "ExecutionStatus": "AVAILABLE", "Changes": []}
        with patch.object(infra, "aws", side_effect=fake_aws), patch.dict(os.environ, {"NETWORK_CFN_ROLE_ARN": "arn:aws:iam::1:role/network"}):
            with self.assertRaisesRegex(infra.InfraError, "StackName"):
                infra.apply_one(self.config_path, report, "audigo-network", SHA)

    def test_apply_executes_only_matching_ready_change_set(self):
        report = self.root / "report.json"
        report.write_text(json.dumps({"version": 1, "commit": SHA, "region": "ap-northeast-2", "stacks": [{"name": "audigo-network", "change_set_arn": "arn:change", "change_type": "UPDATE", "status": "READY", "role_arn": ROLE, "marker": MARKER}]}))
        calls = []
        def fake_aws(args):
            calls.append(args)
            if args[1] == "describe-change-set":
                return {"StackName": "audigo-network", "ChangeSetId": "arn:change", "Description": MARKER, "Status": "CREATE_COMPLETE", "ExecutionStatus": "AVAILABLE", "Changes": []}
            return {}
        with patch.object(infra, "aws", side_effect=fake_aws), patch.dict(os.environ, {"NETWORK_CFN_ROLE_ARN": ROLE}):
            infra.apply_one(self.config_path, report, "audigo-network", SHA)
        self.assertTrue(any(call[1] == "execute-change-set" for call in calls))
        self.assertTrue(any(call[1] == "wait" and "stack-update-complete" in call for call in calls))

    def test_apply_rejects_tampered_type_marker(self):
        report = self.root / "report.json"
        report.write_text(json.dumps({"version": 1, "commit": SHA, "region": "ap-northeast-2", "stacks": [{"name": "audigo-network", "change_set_arn": "arn:change", "change_type": "UPDATE", "status": "READY", "role_arn": ROLE, "marker": "audigo-iac commit=" + SHA + " type=CREATE role=" + ROLE}]}))
        with patch.dict(os.environ, {"NETWORK_CFN_ROLE_ARN": ROLE}):
            with self.assertRaisesRegex(infra.InfraError, "marker"):
                infra.apply_one(self.config_path, report, "audigo-network", SHA)


if __name__ == "__main__":
    unittest.main()
