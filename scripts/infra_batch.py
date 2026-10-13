#!/usr/bin/env python3
"""Plan and apply enabled CloudFormation stacks in dependency order."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import infra


def selected_stacks(config: Path, requested: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data, stacks, _ = infra.validate_config(config)
    ordered = infra.topological_order(stacks, enabled_only=True)
    if not ordered:
        raise infra.InfraError("활성화된 stack이 없습니다.")
    if requested and requested != "all":
        ordered = [s for s in ordered if s["name"] == requested]
        if not ordered:
            raise infra.InfraError(f"활성화된 stack이 아닙니다: {requested}")
    return data, ordered


def ancestors(stack: dict[str, Any], by_name: dict[str, dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    def visit(name: str) -> None:
        for dependency in by_name[name]["depends_on"]:
            if dependency not in result:
                result.add(dependency)
                visit(dependency)
    visit(stack["name"])
    return result


def plan(config: Path, commit: str, report_path: Path, requested: str | None = None) -> dict[str, Any]:
    if not infra.SHA_RE.fullmatch(commit):
        raise infra.InfraError("commit은 40자리 소문자 hexadecimal SHA여야 합니다.")
    data, selected = selected_stacks(config, requested)
    _, all_stacks, _ = infra.validate_config(config)
    by_name = {s["name"]: s for s in all_stacks}
    records: list[dict[str, Any]] = []
    deferred: list[str] = []
    ready: set[str] = set()
    for stack in selected:
        if ancestors(stack, by_name) & ready:
            deferred.append(stack["name"])
            continue
        record = infra.plan_one(config, stack["name"], commit)
        records.append(record)
        if record["status"] == "READY":
            ready.add(stack["name"])
    bundle = {
        "version": 1, "commit": commit, "region": data["region"],
        "selected": [s["name"] for s in selected], "stacks": records, "deferred": deferred,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    has_changes = any(r["status"] == "READY" for r in records)
    value = f"has_changes={'true' if has_changes else 'false'}\n"
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(value)
    print(value, end="")
    if deferred:
        print("DEFERRED: " + ", ".join(deferred) + " (선행 stack 적용 후 다시 계획하세요.)")
    return bundle


def apply(config: Path, commit: str, report_path: Path, requested: str | None = None) -> None:
    if not infra.SHA_RE.fullmatch(commit):
        raise infra.InfraError("commit은 40자리 소문자 hexadecimal SHA여야 합니다.")
    data, selected = selected_stacks(config, requested)
    bundle = infra.load_json(report_path)
    if not isinstance(bundle, dict) or bundle.get("version") != 1:
        raise infra.InfraError("batch report 형식이 올바르지 않습니다.")
    expected = [s["name"] for s in selected]
    records = bundle.get("stacks")
    deferred = bundle.get("deferred")
    if (bundle.get("commit") != commit or bundle.get("region") != data["region"]
            or bundle.get("selected") != expected or not isinstance(records, list)
            or not isinstance(deferred, list)):
        raise infra.InfraError("batch report가 현재 커밋·리전·선택 대상과 일치하지 않습니다.")
    names = [r.get("name") for r in records if isinstance(r, dict)]
    expected_planned = [name for name in expected if name not in deferred]
    if (len(names) != len(records) or len(names) + len(deferred) != len(expected)
            or set(names) | set(deferred) != set(expected) or set(names) & set(deferred)
            or names != expected_planned):
        raise infra.InfraError("batch report의 계획/보류 대상이 선택 대상과 일치하지 않습니다.")
    if deferred:
        print("DEFERRED: " + ", ".join(deferred) + " (선행 stack 적용 후 다시 계획하세요.)")
    for record in records:
        # infra.apply_one intentionally validates the record again before AWS execution.
        single = {"version": 1, "commit": commit, "region": data["region"], "stacks": [record]}
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as handle:
            json.dump(single, handle)
            temp_path = Path(handle.name)
        try:
            infra.apply_one(config, temp_path, record["name"], commit)
        finally:
            temp_path.unlink(missing_ok=True)
    if deferred:
        print("배치 적용 완료: 보류된 stack은 새 계획 생성 후 적용이 필요합니다.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--commit", required=True)
        command.add_argument("--report", type=Path, required=True)
        command.add_argument("--stack")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            plan(args.config, args.commit, args.report, args.stack)
        else:
            apply(args.config, args.commit, args.report, args.stack)
        return 0
    except infra.InfraError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
