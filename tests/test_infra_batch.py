import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).parents[1] / "scripts"
import sys
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("infra_batch", SCRIPTS / "infra_batch.py")
batch = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(batch)
SHA = "b" * 40


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "deploy").mkdir(); (root / "cloudformation").mkdir(); (root / "parameters").mkdir()
        for name in ("a", "b", "c"):
            (root / "cloudformation" / f"{name}.yaml").write_text("Resources: {}\n")
            (root / "parameters" / f"{name}.json").write_text("[]")
        self.config = root / "deploy/stacks.json"; self.report = root / "report.json"
        self.write_config()

    def tearDown(self): self.temp.cleanup()

    def write_config(self, enabled=(True, True, True)):
        stacks = []
        for index, name in enumerate(("a", "b", "c")):
            stacks.append({"name": name, "template": f"cloudformation/{name}.yaml", "parameters": f"parameters/{name}.json", "role_group": "core", "enabled": enabled[index], "allow_create": False, "depends_on": [] if name == "a" else ["a"] if name == "b" else ["b"]})
        self.config.write_text(json.dumps({"region": "r", "execution_roles": {"core": "${INFRA_CORE_CFN_ROLE_ARN}", "platform": "${INFRA_PLATFORM_CFN_ROLE_ARN}", "app": "${INFRA_APP_CFN_ROLE_ARN}"}, "stacks": stacks}))

    def test_plan_nonempty_and_deferred_transitive(self):
        def fake_plan(_, name, __): return {"name": name, "status": "READY", "change_set_arn": name}
        with patch.object(batch.infra, "plan_one", side_effect=fake_plan):
            result = batch.plan(self.config, SHA, self.report, "all")
        self.assertEqual(["a"], [r["name"] for r in result["stacks"]])
        self.assertEqual(["b", "c"], result["deferred"])

    def test_plan_no_changes_is_false(self):
        with patch.object(batch.infra, "plan_one", return_value={"name": "a", "status": "NO_CHANGES"}):
            result = batch.plan(self.config, SHA, self.report, "a")
        self.assertFalse(any(r["status"] == "READY" for r in result["stacks"]))

    def test_apply_stops_after_failure(self):
        report = {"version": 1, "commit": SHA, "region": "r", "selected": ["a"], "deferred": [], "stacks": [{"name": "a", "status": "READY"}]}
        self.report.write_text(json.dumps(report))
        with patch.object(batch.infra, "apply_one", side_effect=batch.infra.InfraError("failed")) as mocked:
            with self.assertRaisesRegex(batch.infra.InfraError, "failed"):
                batch.apply(self.config, SHA, self.report, "a")
        self.assertEqual(1, mocked.call_count)

    def test_rejects_wrong_sha_and_disabled_manual_selection(self):
        with self.assertRaisesRegex(batch.infra.InfraError, "40자리"):
            batch.plan(self.config, "bad", self.report)
        self.write_config(enabled=(True, False, False))
        with self.assertRaisesRegex(batch.infra.InfraError, "활성화"):
            batch.plan(self.config, SHA, self.report, "b")

    def test_rejects_empty_enabled_batch(self):
        self.write_config(enabled=(False, False, False))
        with self.assertRaisesRegex(batch.infra.InfraError, "활성화된 stack이 없습니다"):
            batch.plan(self.config, SHA, self.report)


if __name__ == "__main__": unittest.main()
