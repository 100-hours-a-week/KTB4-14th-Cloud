# CloudFormation CI/CD 운영 가이드

## 현재 구현 범위

- **CI:** `cloudformation/`의 모든 작성된 YAML을 검증한다. 작성 대기 중인 빈 파일은 명시적으로 보고하며, 활성화된 빈 파일은 실패한다.
- **CD:** 인프라 관련 코드가 main에 반영되면 활성 스택 전체의 **계획을 자동 생성 → 팀원 승인 → 적용**한다. 현재 `audigo-network`만 활성화되어 있다. `Run workflow`는 전체/개별 스택 재시도용으로 유지한다.
- 다른 스택은 템플릿과 IAM 준비 후 `deploy/stacks.json`에서 활성화한다. 매번 활성 스택 전체를 실제 AWS 상태와 비교하므로 직전 커밋 diff에 의존하지 않는다. 무변경 스택은 적용하지 않는다.
- 선행 스택에 변경이 있으면 후속 스택 계획은 `deferred`로 표시한다. 선행 적용 후 `Run workflow → all`로 다시 계획한다. 변경될 Outputs를 적용 전에 확정하지 않기 위한 안전장치다.
- `docs/`의 스캔 원본은 배포 대상이 아니다. 기존 AI 릴리스 워크플로는 변경하지 않았다.
- 로컬 파일만 추가했다. IAM 생성, GitHub 설정, 커밋/push, 실제 스택 반영은 수행하지 않았다.

## 파일 구성

```text
.github/workflows/cloudformation-ci.yml  # PR/main 전체 검증, AWS 인증 없음
.github/workflows/cloudformation-cd.yml  # main 자동 계획/승인 후 실행
scripts/infra.py                         # 설정 검증, 개별 Change Set 안전장치
scripts/infra_batch.py                   # 전체 계획/순차 적용, 후속 스택 지연
tests/test_infra.py                      # AWS 응답 mock 테스트
deploy/stacks.json                      # 템플릿 ↔ 스택 및 실행 역할 그룹 매핑
deploy/parameters/*.json                 # 스택별 비밀값 없는 파라미터
bootstrap/infra-iam.yaml                 # 별도 관리자가 적용하는 그룹별 IAM
deploy/iam/*.json                        # 이전 network 전용 참고 정책
```

## 1. IAM 준비 — 별도 관리자 작업

현재 설계는 GitHub Deploy 역할 1개와 Core / Platform / App 실행 역할 3개다.
정책의 기준은 `bootstrap/infra-iam.yaml`이며, 적용 및 기존 역할 전환 절차는
[그룹별 IAM 운영 가이드](iam-group-permissions.md)를 따른다.

- IAM 스택은 일반 인프라 CD 배포 대상에서 제외한다. 관리자가 별도 검토 후 적용한다.
- 기존 `deploy/iam/*.json`은 이전 network 전용 정책 참고 자료이며 새 정책과 중복 적용하지 않는다.
- 단일 GitHub 역할이라도 Plan/Production OIDC subject에 따라 실행 권한을 제한한다.
- 실행 역할을 스택에 연결하기 전에 현재 RoleARN과 신규 정책을 검토한다. 연결된 서비스 역할은 제거할 수 없다.
- 기존 애플리케이션 배포 역할과 런타임 역할은 변경하지 않는다.

## 2. GitHub 설정 — 저장소 관리자 작업

### Environments

`Settings → Environments`에서 다음 환경을 만든다.

| Environment | 설정 |
|---|---|
| `iac-plan` | Deployment branches: main만 허용, Required reviewers 없음 |
| `iac-production` | main만 허용, Required reviewers, Prevent self-review, 관리자 우회 제한 |

Required reviewers 제공 여부는 저장소 공개 여부와 GitHub 요금제에 따라 다르다. 지원되지 않으면 현재 apply job을 승인 없이 활성화하지 않는다. Change Set 계획만 사용하고, 별도 승인된 AWS 관리자 수동 실행 절차를 마련해야 한다. `workflow_dispatch` 자체는 독립 승인이 아니다. [Environment 안내](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)

### Variables

| 위치 | 변수 | 설정 값 |
|---|---|---|
| 두 Environment | `INFRA_DEPLOY_ROLE_ARN` | `AudigoGitHubInfraDeployRole`의 ARN |
| Repository | `INFRA_CORE_CFN_ROLE_ARN` | `AudigoCfnCoreExecutionRole`의 ARN |
| Repository | `INFRA_PLATFORM_CFN_ROLE_ARN` | `AudigoCfnPlatformExecutionRole`의 ARN |
| Repository | `INFRA_APP_CFN_ROLE_ARN` | `AudigoCfnAppExecutionRole`의 ARN |
| 두 Environment | `INFRA_DEPLOY_ENABLED` | 전환 검토 전 `false`, 준비 완료 후 `true` |

역할 ARN은 실제 IAM 스택 적용 결과를 사용한다. 기존 `INFRA_PLAN_ROLE_ARN`,
`INFRA_APPLY_ROLE_ARN`, `NETWORK_CFN_ROLE_ARN`은 전환 검증 후 정리한다.
AWS access key를 저장하지 않고 기존 AI의 AWS_ROLE_ARN도 재사용하지 않는다.

### main 보호

PR 필수, CI의 `validate` 검사 필수, 승인 리뷰 필수로 설정한다. 워크플로/스크립트/IAM 정책 변경도 보안 리뷰한다. 새 GitHub Actions는 공식 release 페이지의 commit 링크로 확인한 전체 SHA로 고정했다. 버전 갱신은 PR로 검증한다. 기존 AI 워크플로의 action 버전은 이번 범위에서 변경하지 않았다.

## 3. 첫 검증

1. PR에서 전체 CI가 통과하는지 확인한다. 현재 브랜치의 작성된 템플릿을 검증하고, 빈 비활성 템플릿은 WAITING으로 표시한다.
2. IAM과 변수, main 브랜치 제한, iac-production 승인자를 설정한 뒤 두 환경의 INFRA_DEPLOY_ENABLED를 문자열 true로 지정한다. 단일 역할의 Plan 세션에는 실행 권한이 없는지 확인한다.
3. 인프라 코드가 main에 반영되면 자동으로 전체 활성 스택 계획을 생성한다. 초기 테스트나 재시도는 `Actions → CloudFormation CD → Run workflow`에서 main, `all` 또는 `audigo-network` 선택.
4. plan job의 Summary/Artifact `plan.json`과 AWS Change Set을 확인한다. 커밋, 대상 스택, 추가/수정 내역을 검토한다.
5. `iac-production` 승인 후 **동일 Change Set ARN**을 실행한다.
6. 모든 스택이 무변경이면 apply job 자체를 건너뛰므로 승인 요청도 발생하지 않는다. 먼저 무변경 배포를 검증한 뒤 안전한 태그 변경으로 승인/UPDATE_COMPLETE/ID 유지/Drift를 확인한다.

plan이 통과해도 승인 환경이 설정되지 않으면 apply는 자동 시작될 수 있다. 반드시 2번 환경 보호를 설정하고 나서 활성화한다.

### 안전장치와 실패 처리

- 삭제(`Remove`), 교체(`True`/`Conditional`) 변경은 실행 차단한다. Retain이 있어도 관리 이탈을 막기 위해 Remove 자체를 차단한다.
- IMPORT는 이 CD에서 수행하지 않는다. 기존 스택이 없으면 기본적으로 실패하며 자동 새 스택을 만들지 않는다.
- CREATE는 `allow_create: true` 및 별도 IAM/신규 생성 검토가 필요하다.
- 스택/선행 스택이 안정 상태가 아니면 계획을 차단한다. 의존성은 배포 설정에 명시하며 실제 파라미터/Outputs 전달은 자동 구현하지 않았다.
- 워크플로 전체 배포는 직렬화한다. 실행 중 업데이트를 취소하지 않는다. GitHub concurrency는 모든 요청을 FIFO로 보존하는 큐가 아니다.
- 실패하면 후속 실행을 중단한다. 롤백 상태/이벤트를 확인한 후 새 계획으로 재시도한다. 기존 Change Set 자동 재실행은 하지 않는다.
- AWS CLI waiter 시간보다 업데이트가 오래 걸리면 워크플로 실패와 AWS 진행 상태가 다를 수 있다. 상태를 확인한 후 재시도한다.
- 실행하지 않은 실패/무변경 Change Set은 남을 수 있다. 정리는 별도 관리자 작업으로 한다(배포 역할에 Delete 권한 없음).

## 4. 다음 스택 활성화

1. 해당 YAML을 작성하거나 기존 리소스를 별도 Import한다.
2. 해당 그룹 실행 역할의 필요한 권한과 제한된 caller 권한을 검토한다.
3. 파라미터 JSON에 비밀값 없는 값 또는 `UsePreviousValue`를 지정한다. 비밀번호/토큰은 Git에 넣지 않고 Secrets Manager 등에서 참조한다.
4. `deploy/stacks.json`의 `role_group`, `depends_on`, 필요한 `capabilities`를 설정하고 `enabled: true`로 변경한다.
5. 그룹 ARN 변수가 IAM 정책의 실제 실행 역할 ARN과 일치하는지 확인한다.
6. 선행 스택부터 검증하고 활성화한다. 자동 계획에서 deferred가 표시되면 선행 적용 후 수동 재실행한다. 실제 파라미터 전달 설정도 별도로 검토한다.

파라미터 예:

```json
[
  {"ParameterKey": "VpcId", "ParameterValue": "vpc-04129874f9fd135c6"},
  {"ParameterKey": "ExistingSetting", "UsePreviousValue": true}
]
```

## 로컬 검증

```bash
python3 -m venv /tmp/audigo-ci
/tmp/audigo-ci/bin/pip install -r deploy/requirements-ci.txt
PATH="/tmp/audigo-ci/bin:$PATH" python3 scripts/infra.py validate --config deploy/stacks.json --lint
python3 -m unittest discover -s tests -v
python3 scripts/infra.py list --config deploy/stacks.json
git diff --check
```

실제 AWS 인증 없이 테스트한다. cfn-lint는 버전을 고정했으며 리소스 스키마 갱신이 필요하면 PR로 버전을 검증·업데이트한다. 모든 비어 있지 않은 템플릿에 대해 warning을 포함한 lint 실패를 차단한다.

### 자동 실행 범위

`cloudformation/`, `deploy/`, 인프라 스크립트/테스트, 인프라 CI/CD 워크플로가 main에 변경되면 계획을 생성한다. `releases/ai.yml`만 변경한 경우에는 인프라 CD를 실행하지 않는다. 앱 배포와 인프라 배포의 concurrency 그룹은 별개이며, 향후 같은 ECS 서비스를 관리할 때 동시 실행 방지 규칙을 추가해야 한다.

## 그룹 권한 전환 시 배포 대상 선택

현재 push 이벤트는 변경 파일 하나만이 아니라 **활성 스택 전체**를 계획한다. PR을 하나씩 병합해도 다른 활성 스택에 변경이 있으면 함께 계획될 수 있다. 검증 단계에서는 다른 스택을 활성화하지 않거나, `Run workflow → main → 개별 스택`으로 대상을 명시한다. `INFRA_DEPLOY_ENABLED=false`는 실행을 보류하기 위한 전환 안전장치이며 AWS 측 역할 권한을 제거하는 기능은 아니다.

DNS 템플릿은 아직 별도 PR에 있으므로 이 브랜치에는 `audigo-dns`를 등록하지 않았다. DNS PR을 병합할 때 core 그룹과 개별 workflow 선택지를 등록하고 CI를 재검증한다. 이 브랜치에서 다른 PR 템플릿을 복사하거나 스택을 자동 활성화하지 않는다.
