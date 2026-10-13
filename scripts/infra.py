#!/usr/bin/env python3
"""Safe, small CloudFormation validation and change-set deployment helper.

The tool deliberately separates planning from applying.  It never imports
resources and it will not execute a change set that removes or replaces a
resource.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ROLE_PLACEHOLDER = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")
REQUIRED_KEYS = {
    "name", "template", "parameters", "role_arn", "enabled", "allow_create", "depends_on"
}
OPTIONAL_KEYS = {"capabilities"}
STABLE_STACK_STATUSES = {
    "CREATE_COMPLETE", "UPDATE_COMPLETE", "IMPORT_COMPLETE", "UPDATE_ROLLBACK_COMPLETE",
}


class InfraError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InfraError(f"JSON을 읽을 수 없습니다: {path}: {exc}") from exc


def repository_root(config_path: Path) -> Path:
    # deploy/stacks.json is intentionally repository-relative.  This also
    # keeps the command usable from GitHub Actions' arbitrary working dir.
    return config_path.resolve().parent.parent


def config_path_value(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if root != path and root not in path.parents:
        raise InfraError(f"저장소 밖 경로는 사용할 수 없습니다: {value}")
    return path


def validate_config(config_path: Path, run_lint: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    data = load_json(config_path)
    root = repository_root(config_path)
    if not isinstance(data, dict) or set(data) - {"region", "stacks"}:
        raise InfraError("설정 최상위에는 region, stacks만 사용할 수 있습니다.")
    if not isinstance(data.get("region"), str) or not data["region"]:
        raise InfraError("region은 비어 있지 않은 문자열이어야 합니다.")
    stacks = data.get("stacks")
    if not isinstance(stacks, list) or not stacks:
        raise InfraError("stacks는 비어 있지 않은 배열이어야 합니다.")

    names: set[str] = set()
    templates: set[str] = set()
    messages: list[str] = []
    for stack in stacks:
        if not isinstance(stack, dict) or not REQUIRED_KEYS.issubset(stack) or set(stack) - (REQUIRED_KEYS | OPTIONAL_KEYS):
            raise InfraError(f"각 stack은 다음 필수 키와 선택 capabilities만 사용할 수 있습니다: {sorted(REQUIRED_KEYS)}")
        name, template, parameters, role = (stack[k] for k in ("name", "template", "parameters", "role_arn"))
        if not all(isinstance(v, str) and v for v in (name, template, parameters)) or not isinstance(role, str):
            raise InfraError(f"{name or '<unknown>'}: name/template/parameters/role_arn은 문자열이어야 합니다.")
        if name in names:
            raise InfraError(f"중복 stack 이름: {name}")
        if template in templates:
            raise InfraError(f"중복 template 경로: {template}")
        names.add(name)
        templates.add(template)
        if not isinstance(stack["enabled"], bool) or not isinstance(stack["allow_create"], bool):
            raise InfraError(f"{name}: enabled와 allow_create는 boolean이어야 합니다.")
        if stack["enabled"] and not role:
            raise InfraError(f"{name}: 활성화된 stack에는 role_arn이 필요합니다.")
        if "capabilities" in stack and (not isinstance(stack["capabilities"], list) or not all(isinstance(v, str) for v in stack["capabilities"])):
            raise InfraError(f"{name}: capabilities는 문자열 배열이어야 합니다.")
        if not isinstance(stack["depends_on"], list) or not all(isinstance(v, str) for v in stack["depends_on"]):
            raise InfraError(f"{name}: depends_on은 문자열 배열이어야 합니다.")
        template_path = config_path_value(root, template)
        parameters_path = config_path_value(root, parameters)
        if not template_path.is_file():
            raise InfraError(f"{name}: template 파일이 없습니다: {template}")
        if not parameters_path.is_file():
            raise InfraError(f"{name}: parameters 파일이 없습니다: {parameters}")
        parameter_values = load_json(parameters_path)
        if not isinstance(parameter_values, list):
            raise InfraError(f"{name}: parameters 파일은 JSON 배열이어야 합니다.")
        validate_parameters(name, parameter_values)
        if not template_path.read_text(encoding="utf-8").strip():
            if stack["enabled"]:
                raise InfraError(f"{name}: 활성화된 stack의 template은 비어 있을 수 없습니다.")
            messages.append(f"WAITING {name}: 비어 있는 작성 대기 템플릿")
        if run_lint and template_path.read_text(encoding="utf-8").strip():
            try:
                result = subprocess.run(["cfn-lint", str(template_path)], text=True, capture_output=True)
            except OSError as exc:
                raise InfraError(f"{name}: cfn-lint을 실행할 수 없습니다: {exc}") from exc
            if result.returncode:
                detail = (result.stdout + result.stderr).strip()
                raise InfraError(f"{name}: cfn-lint 실패\n{detail}")

    for stack in stacks:
        missing = set(stack["depends_on"]) - names
        if missing:
            raise InfraError(f"{stack['name']}: 없는 의존 stack: {', '.join(sorted(missing))}")
        if stack["name"] in stack["depends_on"]:
            raise InfraError(f"{stack['name']}: 자기 자신에 의존할 수 없습니다.")

    configured = {str(config_path_value(root, s["template"]).relative_to(root)) for s in stacks}
    cloudformation = root / "cloudformation"
    actual = {str(p.relative_to(root)) for p in cloudformation.rglob("*.yaml")} | {str(p.relative_to(root)) for p in cloudformation.rglob("*.yml")}
    missing_config = actual - configured
    extra_config = configured - actual
    if missing_config:
        raise InfraError(f"등록되지 않은 CloudFormation 템플릿: {', '.join(sorted(missing_config))}")
    if extra_config:
        raise InfraError(f"cloudformation/ 아래에 없는 template 등록: {', '.join(sorted(extra_config))}")
    topological_order(stacks)  # cycle validation
    return data, stacks, messages


def topological_order(stacks: list[dict[str, Any]], enabled_only: bool = False) -> list[dict[str, Any]]:
    selected = {s["name"]: s for s in stacks if not enabled_only or s["enabled"]}
    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[dict[str, Any]] = []

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise InfraError(f"의존성 순환이 있습니다: {name}")
        visiting.add(name)
        for dependency in selected[name]["depends_on"]:
            if dependency in selected:
                visit(dependency)
        visiting.remove(name)
        visited.add(name)
        ordered.append(selected[name])

    for item in stacks:
        if item["name"] in selected:
            visit(item["name"])
    return ordered


def aws(args: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(["aws", *args, "--no-cli-pager"], text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise InfraError("AWS CLI를 찾을 수 없습니다. aws CLI를 설치하거나 PATH를 확인하세요.") from exc
    if result.returncode:
        raise InfraError((result.stderr or result.stdout or "AWS CLI 실행 실패").strip())
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise InfraError(f"AWS CLI JSON 응답을 해석할 수 없습니다: {exc}") from exc


def validate_parameters(stack_name: str, values: list[Any]) -> None:
    allowed = {"ParameterKey", "ParameterValue", "UsePreviousValue"}
    for index, value in enumerate(values):
        if not isinstance(value, dict) or set(value) - allowed:
            raise InfraError(f"{stack_name}: parameters[{index}]에 허용되지 않은 키가 있습니다.")
        if not isinstance(value.get("ParameterKey"), str) or not value["ParameterKey"]:
            raise InfraError(f"{stack_name}: parameters[{index}].ParameterKey는 비어 있지 않은 문자열이어야 합니다.")
        has_value = "ParameterValue" in value
        previous = value.get("UsePreviousValue")
        if has_value and not isinstance(value["ParameterValue"], str):
            raise InfraError(f"{stack_name}: parameters[{index}].ParameterValue는 문자열이어야 합니다.")
        if has_value == (previous is True):
            raise InfraError(f"{stack_name}: parameters[{index}]에는 ParameterValue 또는 UsePreviousValue: true 중 하나만 필요합니다.")
        if not has_value and previous is not True:
            raise InfraError(f"{stack_name}: parameters[{index}].UsePreviousValue는 true여야 합니다.")


def resolve_role(value: str) -> str:
    match = ROLE_PLACEHOLDER.fullmatch(value)
    if not match:
        return value
    resolved = os.environ.get(match.group(1))
    if not resolved:
        raise InfraError(f"배포 시 필요한 환경 변수 {match.group(1)}가 없습니다.")
    return resolved


def parameters_argument(root: Path, stack: dict[str, Any]) -> str:
    path = config_path_value(root, stack["parameters"])
    values = load_json(path)
    if not isinstance(values, list):
        raise InfraError(f"{stack['name']}: parameters 파일은 JSON 배열이어야 합니다.")
    validate_parameters(stack["name"], values)
    return f"file://{path}"


def is_stack_missing(error: InfraError) -> bool:
    text = str(error).lower()
    return "validationerror" in text and "stack with id" in text and "does not exist" in text


def describe_stack(region: str, name: str) -> dict[str, Any] | None:
    try:
        response = aws(["cloudformation", "describe-stacks", "--region", region, "--stack-name", name, "--output", "json"])
        stacks = response.get("Stacks")
        if not isinstance(stacks, list) or len(stacks) != 1 or not isinstance(stacks[0], dict):
            raise InfraError(f"{name}: DescribeStacks 응답이 올바르지 않습니다.")
        return stacks[0]
    except InfraError as exc:
        if is_stack_missing(exc):
            return None
        raise


def require_stable_stack(region: str, name: str, label: str) -> dict[str, Any]:
    stack = describe_stack(region, name)
    if stack is None:
        raise InfraError(f"{label} {name}: CloudFormation stack이 존재하지 않습니다.")
    status = stack.get("StackStatus")
    if status not in STABLE_STACK_STATUSES:
        raise InfraError(f"{label} {name}: 안정 상태가 아닙니다 ({status}).")
    return stack


def change_set_name(stack: str, commit: str) -> str:
    return f"iac-{commit[:12]}-{int(time.time())}-{stack}"[:128]


def change_marker(commit: str, change_type: str, role: str) -> str:
    return f"audigo-iac commit={commit} type={change_type} role={role}"


def change_set_args(root: Path, region: str, stack: dict[str, Any], commit: str, exists: bool, marker: str) -> list[str]:
    role = resolve_role(stack["role_arn"])
    args = [
        "cloudformation", "create-change-set", "--region", region,
        "--stack-name", stack["name"], "--change-set-name", change_set_name(stack["name"], commit),
        "--change-set-type", "UPDATE" if exists else "CREATE",
        "--template-body", f"file://{config_path_value(root, stack['template'])}",
        "--parameters", parameters_argument(root, stack), "--role-arn", role,
        "--description", marker, "--output", "json",
    ]
    capabilities = stack.get("capabilities", [])
    if capabilities:
        args.extend(["--capabilities", *capabilities])
    return args


def no_changes(description: dict[str, Any], change_type: str) -> bool:
    reason = str(description.get("StatusReason", "")).lower()
    return (change_type == "UPDATE" and description.get("Status") == "FAILED"
            and ("didn't contain changes" in reason or "no updates are to be performed" in reason))


def unsafe_changes(description: dict[str, Any]) -> list[str]:
    unsafe: list[str] = []
    for item in description.get("Changes", []):
        resource = item.get("ResourceChange", {})
        replacement = resource.get("Replacement")
        if resource.get("Action") == "Remove" or replacement in ("True", "Conditional"):
            unsafe.append(resource.get("LogicalResourceId", "<unknown>"))
    return unsafe


def review_changes(description: dict[str, Any]) -> list[dict[str, str | None]]:
    """Keep only non-sensitive Change Set metadata in the approval report."""
    result: list[dict[str, str | None]] = []
    for item in description.get("Changes", []):
        resource = item.get("ResourceChange", {})
        result.append({
            "action": resource.get("Action"),
            "logical_resource_id": resource.get("LogicalResourceId"),
            "resource_type": resource.get("ResourceType"),
            "replacement": resource.get("Replacement"),
        })
    return result


def verify_change_set_identity(description: dict[str, Any], stack_name: str, arn: str, marker: str) -> None:
    described_stack = description.get("StackName")
    if described_stack != stack_name:
        raise InfraError("Change Set의 StackName이 report 대상과 일치하지 않습니다.")
    stack_id = description.get("StackId")
    if stack_id is not None and (not isinstance(stack_id, str) or f":stack/{stack_name}/" not in stack_id):
        raise InfraError("Change Set의 StackId가 report 대상과 일치하지 않습니다.")
    if description.get("ChangeSetId") != arn:
        raise InfraError("Change Set ID가 report의 ARN과 일치하지 않습니다.")
    if description.get("Description") != marker:
        raise InfraError("Change Set 설명이 승인된 계획 marker와 일치하지 않습니다.")


def plan_one(config_path: Path, stack_name: str, commit: str) -> dict[str, Any]:
    if not SHA_RE.fullmatch(commit):
        raise InfraError("commit은 40자리 소문자 hexadecimal SHA여야 합니다.")
    data, stacks, _ = validate_config(config_path)
    root = repository_root(config_path)
    stack = next((s for s in stacks if s["name"] == stack_name), None)
    if not stack or not stack["enabled"]:
        raise InfraError(f"활성화된 stack이 아닙니다: {stack_name}")
    for dependency in stack["depends_on"]:
        require_stable_stack(data["region"], dependency, "선행 stack")
    current = describe_stack(data["region"], stack_name)
    exists = current is not None
    if current is not None and current.get("StackStatus") not in STABLE_STACK_STATUSES:
        raise InfraError(f"{stack_name}: 안정 상태가 아닙니다 ({current.get('StackStatus')}).")
    if not exists and not stack["allow_create"]:
        raise InfraError(f"{stack_name}: 존재하지 않는 stack 생성은 허용되지 않습니다.")
    change_type = "UPDATE" if exists else "CREATE"
    role = resolve_role(stack["role_arn"])
    marker = change_marker(commit, change_type, role)
    created = aws(change_set_args(root, data["region"], stack, commit, exists, marker))
    arn = created.get("Id")
    if not arn:
        raise InfraError("CreateChangeSet 응답에 Id가 없습니다.")
    wait_error = None
    try:
        aws(["cloudformation", "wait", "change-set-create-complete", "--region", data["region"], "--stack-name", stack_name, "--change-set-name", arn])
    except InfraError as exc:
        wait_error = str(exc)
    description = aws(["cloudformation", "describe-change-set", "--region", data["region"], "--stack-name", stack_name, "--change-set-name", arn, "--output", "json"])
    verify_change_set_identity(description, stack_name, arn, marker)
    if no_changes(description, change_type):
        return {
            "name": stack_name, "region": data["region"], "change_set_arn": arn,
            "change_type": change_type, "status": "NO_CHANGES", "role_arn": role, "marker": marker,
            "changes": [],
        }
    if wait_error:
        raise InfraError(f"{stack_name}: Change Set 생성 실패: {wait_error}")
    if description.get("Status") != "CREATE_COMPLETE":
        raise InfraError(f"{stack_name}: Change Set 상태가 CREATE_COMPLETE가 아닙니다: {description.get('Status')}")
    unsafe = unsafe_changes(description)
    if unsafe:
        raise InfraError(f"{stack_name}: 삭제 또는 교체 변경은 실행할 수 없습니다: {', '.join(unsafe)}")
    return {
        "name": stack_name, "region": data["region"], "change_set_arn": arn,
        "change_type": change_type, "status": "READY", "role_arn": role, "marker": marker,
        "changes": review_changes(description),
    }


def write_report(path: Path, commit: str, record: dict[str, Any]) -> None:
    report = {"version": 1, "commit": commit, "region": record["region"], "stacks": [record]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def apply_one(config_path: Path, report_path: Path, stack_name: str, commit: str) -> None:
    if not SHA_RE.fullmatch(commit):
        raise InfraError("commit은 40자리 소문자 hexadecimal SHA여야 합니다.")
    data, stacks, _ = validate_config(config_path)
    report = load_json(report_path)
    if (not isinstance(report, dict) or report.get("commit") != commit
            or report.get("region") != data["region"] or not isinstance(report.get("stacks"), list)):
        raise InfraError("report의 commit 또는 stacks 형식이 올바르지 않습니다.")
    stack = next((s for s in stacks if s["name"] == stack_name and s["enabled"]), None)
    records = [r for r in report["stacks"] if isinstance(r, dict) and r.get("name") == stack_name]
    if not stack or len(records) != 1:
        raise InfraError("report와 활성 stack 구성이 일치하지 않습니다.")
    record = records[0]
    role = resolve_role(stack["role_arn"])
    if record.get("role_arn") != role or record.get("status") not in ("READY", "NO_CHANGES"):
        raise InfraError("report의 역할 또는 상태가 현재 설정과 일치하지 않습니다.")
    if record.get("change_type") == "CREATE" and not stack["allow_create"]:
        raise InfraError("현재 설정은 CREATE Change Set 실행을 허용하지 않습니다.")
    expected_marker = change_marker(commit, record.get("change_type"), role)
    if record.get("marker") != expected_marker:
        raise InfraError("report의 Change Set marker가 현재 커밋·유형·역할과 일치하지 않습니다.")
    if record["status"] == "NO_CHANGES":
        return
    arn = record.get("change_set_arn")
    if not isinstance(arn, str) or not arn:
        raise InfraError("report에 Change Set ARN이 없습니다.")
    description = aws(["cloudformation", "describe-change-set", "--region", data["region"], "--stack-name", stack_name, "--change-set-name", arn, "--output", "json"])
    verify_change_set_identity(description, stack_name, arn, expected_marker)
    if description.get("Status") != "CREATE_COMPLETE":
        raise InfraError("승인된 Change Set이 더 이상 CREATE_COMPLETE 상태가 아닙니다.")
    if description.get("ExecutionStatus") != "AVAILABLE":
        raise InfraError("승인된 Change Set을 실행할 수 없는 상태입니다.")
    unsafe = unsafe_changes(description)
    if unsafe:
        raise InfraError(f"삭제 또는 교체 변경은 실행할 수 없습니다: {', '.join(unsafe)}")
    aws(["cloudformation", "execute-change-set", "--region", data["region"], "--stack-name", stack_name, "--change-set-name", arn])
    waiter = "stack-update-complete" if record.get("change_type") == "UPDATE" else "stack-create-complete"
    aws(["cloudformation", "wait", waiter, "--region", data["region"], "--stack-name", stack_name])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--config", required=True, type=Path)
    validate.add_argument("--lint", action="store_true")
    listing = sub.add_parser("list")
    listing.add_argument("--config", required=True, type=Path)
    plan = sub.add_parser("plan")
    plan.add_argument("--config", required=True, type=Path)
    plan.add_argument("--stack", required=True)
    plan.add_argument("--commit", required=True)
    plan.add_argument("--report", required=True, type=Path)
    apply = sub.add_parser("apply")
    apply.add_argument("--config", required=True, type=Path)
    apply.add_argument("--stack", required=True)
    apply.add_argument("--commit", required=True)
    apply.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            _, _, messages = validate_config(args.config, args.lint)
            print("VALID")
            print("\n".join(messages))
        elif args.command == "list":
            _, stacks, _ = validate_config(args.config)
            print(json.dumps([s["name"] for s in topological_order(stacks, enabled_only=True)]))
        elif args.command == "plan":
            record = plan_one(args.config, args.stack, args.commit)
            write_report(args.report, args.commit, record)
            print(json.dumps(record, sort_keys=True))
        else:
            apply_one(args.config, args.report, args.stack, args.commit)
            print(f"APPLIED {args.stack}")
        return 0
    except InfraError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
