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
deploy/stacks.json                      # 11개 파일 ↔ 스택 매핑
deploy/parameters/*.json                 # 스택별 비밀값 없는 파라미터
deploy/iam/*.json                        # 최초 network용 최소 권한 정책
```

## 1. IAM 준비 — AWS 관리자 작업

GitHub용 역할을 계획/적용으로 분리하고, CloudFormation 실행 역할을 별도로 만든다.

| 제안 역할 | 신뢰 정책 | 권한 정책 |
|---|---|---|
| `audigo-infra-plan` | `github-plan-trust.example.json` | `github-plan-policy.json` |
| `audigo-infra-apply` | `github-apply-trust.example.json` | `github-apply-policy.json` |
| `audigo-cfn-network-execution` | `network-execution-trust.json` | `network-execution-policy.json` |

파일 위치는 `deploy/iam/`이다. 계정 `185489190599`, 서울 리전, 현재 기존 네트워크 5개 ID를 기준으로 작성했다. 실제 계정/역할명/리소스 ID를 먼저 확인한다.

### OIDC 신뢰 정책의 placeholder를 반드시 교체

`<EXACT_OIDC_SUB_FOR_...>`는 **실제 저장소의 정확한 OIDC subject**로 교체해야 한다. placeholder 그대로는 인증이 불가능하도록 작성했다. 광범위한 `repo:*` 와일드카드로 해결하지 않는다.

기존 형식 예시:

```text
repo:100-hours-a-week/KTB4-14th-Cloud:environment:iac-plan
repo:100-hours-a-week/KTB4-14th-Cloud:environment:iac-production
```

단, 2026-07-15 이후 생성된 저장소 또는 새 형식에 opt-in한 저장소에는 조직/저장소 ID가 `@ID`로 포함될 수 있다. 위 예시를 추측으로 복사하지 말고 실제 subject 형식을 확인한다. 조직의 사용자 지정 subject 설정도 확인한다. [OIDC 공식 안내](https://github.com/aws-actions/configure-aws-credentials#oidc-configuration)

- OIDC provider: `token.actions.githubusercontent.com`, audience: `sts.amazonaws.com`.
- plan 역할에는 ExecuteChangeSet 권한이 없다.
- apply 역할에는 CreateChangeSet/PassRole/EC2 직접 변경 권한이 없다.
- CloudFormation 작업은 현재 `audigo-network` 스택 ARN에 한정한다. 다른 스택을 활성화할 때 해당 스택과 역할만 개별 추가한다.
- 실행 역할은 현재 5개 리소스의 태그 변경과 필요한 EC2 조회만 허용한다. **Private Subnet 생성 등은 아직 허용하지 않는다.** 생성/설정 변경 필요 시 해당 작업 및 롤백 권한을 별도로 설계한다.
- 정확한 CloudFormation 리소스 공급자의 필요 조회 권한은 실제 첫 배포에서 확인해야 한다. AccessDenied 발생 시 실패한 작업만 검토하여 보완한다. AdministratorAccess/EC2FullAccess를 추가하지 않는다.
- CFN CreateChangeSet/DescribeChangeSet/ExecuteChangeSet은 서비스 권한 표의 **stack 리소스**를 기준으로 제한했다. [서비스 권한 표](https://docs.aws.amazon.com/service-authorization/latest/reference/list_cloudformation.html)
- 실행 역할을 스택에 연결하면 이후에도 사용되는 권한 경계다. 스택 작업 권한을 가진 다른 사용자도 간접적으로 해당 역할을 사용할 수 있으므로 접근을 제한한다. [서비스 역할 안내](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-iam-servicerole.html)

## 2. GitHub 설정 — 저장소 관리자 작업

### Environments

`Settings → Environments`에서 다음 환경을 만든다.

| Environment | 설정 |
|---|---|
| `iac-plan` | Deployment branches: main만 허용, Required reviewers 없음 |
| `iac-production` | main만 허용, Required reviewers, Prevent self-review, 관리자 우회 제한 |

Required reviewers 제공 여부는 저장소 공개 여부와 GitHub 요금제에 따라 다르다. 지원되지 않으면 현재 apply job을 승인 없이 활성화하지 않는다. Change Set 계획만 사용하고, 별도 승인된 AWS 관리자 수동 실행 절차를 마련해야 한다. `workflow_dispatch` 자체는 독립 승인이 아니다. [Environment 안내](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)

### Variables

| 위치 | 변수 | 예시 |
|---|---|---|
| `iac-plan` | `INFRA_PLAN_ROLE_ARN` | `arn:aws:iam::185489190599:role/audigo-infra-plan` |
| `iac-production` | `INFRA_APPLY_ROLE_ARN` | `arn:aws:iam::185489190599:role/audigo-infra-apply` |
| Repository | `NETWORK_CFN_ROLE_ARN` | `arn:aws:iam::185489190599:role/audigo-cfn-network-execution` |
| 각 Environment | `INFRA_DEPLOY_ENABLED` | 준비 완료 후에만 문자열 `true` |

`NETWORK_CFN_ROLE_ARN`은 현재 IAM 정책에 적힌 역할 ARN과 일치해야 한다. AWS access key를 Secrets에 저장하지 않는다. 기존 AI의 AWS_ROLE_ARN은 재사용하지 않는다.

### main 보호

PR 필수, CI의 `validate` 검사 필수, 승인 리뷰 필수로 설정한다. 워크플로/스크립트/IAM 정책 변경도 보안 리뷰한다. 새 GitHub Actions는 공식 release 페이지의 commit 링크로 확인한 전체 SHA로 고정했다. 버전 갱신은 PR로 검증한다. 기존 AI 워크플로의 action 버전은 이번 범위에서 변경하지 않았다.

## 3. 첫 검증

1. PR에서 전체 CI가 통과하는지 확인한다. 현재 network는 검증, 다른 10개는 WAITING 표시가 정상이다.
2. IAM과 변수, main 브랜치 제한, iac-production 승인자를 설정한 뒤 두 환경의 INFRA_DEPLOY_ENABLED를 문자열 true로 지정한다. 계획 역할은 생성만, 적용 역할은 실행만 허용되었는지 확인한다.
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
2. 스택별 실행 역할과 제한된 caller 권한을 준비한다.
3. 파라미터 JSON에 비밀값 없는 값 또는 `UsePreviousValue`를 지정한다. 비밀번호/토큰은 Git에 넣지 않고 Secrets Manager 등에서 참조한다.
4. `deploy/stacks.json`의 `role_arn`, `depends_on`, 필요한 `capabilities`를 설정하고 `enabled: true`로 변경한다.
5. 해당 역할 ARN을 workflow env에 전달하도록 추가한다(또는 설정에 비밀값이 아닌 정확한 ARN을 선언한다).
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
