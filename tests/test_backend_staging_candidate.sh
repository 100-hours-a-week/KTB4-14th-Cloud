#!/usr/bin/env bash
set -euo pipefail

valid='{"schema_version":1,"source_repository":"100-hours-a-week/KTB4-14th-BE","source_sha":"0123456789abcdef0123456789abcdef01234567","source_ref":"refs/heads/main","image_repository":"mingkiboo/audigo-backend","image_tag":"sha-0123456789abcdef0123456789abcdef01234567","image_digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","platform":"linux/arm64","workflow_run_id":"123","workflow_run_attempt":"2","workflow_run_url":"https://github.com/100-hours-a-week/KTB4-14th-BE/actions/runs/123/attempts/2"}'

validate() {
  PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN:-python3}" -c '
import sys
sys.path.insert(0, "scripts")
import backend_staging_values as values
values.validate_candidate(values.load_json(sys.stdin.read()))
' >/dev/null 2>&1
}

printf '%s' "$valid" | validate
! printf '%s' "$valid" | jq '.image_repository="evil/image"' | validate
! printf '%s' "$valid" | jq '.image_digest="sha256:ABC"' | validate
! printf '%s' "$valid" | jq '.workflow_run_attempt="02"' | validate
