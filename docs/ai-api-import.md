# AI Worker Service 편입(IMPORT)

> **2026-10-19 보류.** `audigo-ai-api` 스택은 `enabled: false`로 비활성화했습니다.
> 서비스가 콘솔 스택 `ECS-Console-V2-Service-audigo-mock-ai-service-audigo-ecs-cluster-3c9a2110`에 속해 있고,
> 이 스택이 `CREATE_FAILED`(롤백 비활성) 상태로 남아 있어 안전하게 관리를 해제할 방법이 없습니다.
> - `rollback-stack`, `delete-stack`: Retain이 없어 운영 중인 서비스가 삭제될 수 있음
> - `update-stack --disable-rollback`: 실패한 리소스 재생성을 시도해 서비스에 영향 가능
> 템플릿·IAM 권한은 유지하며, 서비스 재생성 등 정리 방법을 점검 시간에 별도로 계획한 뒤 재개합니다.

운영 중인 `audigo-mock-ai-service`를 새 스택 `audigo-ai-api`로 IMPORT합니다. 실제 서비스는 바뀌지 않습니다.

## 관리 경계

| 대상 | 관리 주체 |
|---|---|
| Task Definition (이미지, 환경 변수, taskRoleArn) | `ai-deploy.yml` |
| SSM 활성 Worker 버전 | `ai-deploy.yml` |
| ECS Service (배포 방식, 배포 설정, 태그 설정) | `audigo-ai-api` 스택 |
| Service가 쓰는 리비전 | family 이름 `audigo-mock-ai` 참조 (최신 ACTIVE 리비전) |
| 실행 태스크 개수 | 현재 Auto Scaling 없음. `DesiredCount`는 선언하지 않음 (현재 1 유지) |
| 배포 실패 리비전 | `ai-deploy.yml`이 실패 시 deregister (추가 예정) |

## 기준 조회 결과 (2026-10-18)

| 항목 | 값 |
|---|---|
| 서비스 | `audigo-mock-ai-service` / 클러스터 `audigo-ecs-cluster` |
| 실행 중인 리비전 | `audigo-mock-ai:8` |
| 실행 방식 | `EC2` (awsvpc 네트워크 설정, 로드 밸런서, 서비스 디스커버리 없음) |
| 배포 설정 | ROLLING, 최대 100% / 최소 0%, 서킷브레이커 롤백 (BOUNDED_PERCENT 50) |
| Auto Scaling | 없음 (`ScalableTargets: []`) |

## 생성·변경 파일

| 파일 | 내용 |
|---|---|
| `cloudformation/ai-api.yaml` | Service 1개, 조회 값 그대로. Task Definition은 family만 참조 |
| `deploy/imports/ai-api-resources-to-import.json` | IMPORT 대상 식별자 |
| `deploy/stacks.json` | `audigo-ai-api` → `role_group: app`, `enabled: true`, `allow_create: false` |
| `bootstrap/infra-iam.yaml` | GitHub 배포 역할에 `audigo-ai-api` 스택 추가, App 실행 역할에 AI Service 조회·수정 권한 추가 |
| `tests/test_ai_api_import_contract.py` | 편입 계약 테스트 |

## 적용 절차

`ai-deploy.yml`이 실행 중이 아닐 때 진행합니다. AI 배포 중에는 인프라 배포를 하지 않습니다.

1. **관리자: IAM 적용.** `bootstrap/infra-iam.yaml` 변경을 Change Set으로 적용합니다.
2. **서비스 소유 스택 확인.** 콘솔에서 만든 서비스는 `ECS-Console-V2-Service-...` 스택이 관리하고 있을 수 있습니다. 리소스는 한 스택에만 속할 수 있으므로 먼저 확인합니다.
   ```bash
   aws cloudformation describe-stack-resources \
     --physical-resource-id arn:aws:ecs:ap-northeast-2:185489190599:service/audigo-ecs-cluster/audigo-mock-ai-service \
     --query 'StackResources[].[StackName,LogicalResourceId]' --output table
   ```
   결과가 있으면 IMPORT 전에 기존 스택에 `Retain`을 적용하고 관리를 해제해야 합니다. 절차는 `docs/rest-api-import.md`의 2~3단계와 같습니다. 오류(스택 없음)가 나오면 바로 다음 단계로 갑니다.
3. **배치 설정 확인.** EC2 실행 방식이라 배치 전략이 있으면 템플릿에 추가해야 합니다.
   ```bash
   aws ecs describe-services --cluster audigo-ecs-cluster --services audigo-mock-ai-service \
     --query 'services[0].{constraints:placementConstraints,strategy:placementStrategy}' --output json
   ```
   둘 다 빈 배열이면 그대로 진행합니다. 값이 있으면 템플릿에 `PlacementConstraints`, `PlacementStrategy`를 추가한 뒤 진행합니다.
4. **IMPORT Change Set 생성·확인.**
   ```bash
   aws cloudformation create-change-set --stack-name audigo-ai-api \
     --change-set-name import-ai-service --change-set-type IMPORT \
     --template-body file://cloudformation/ai-api.yaml \
     --resources-to-import file://deploy/imports/ai-api-resources-to-import.json \
     --role-arn <AudigoCfnAppExecutionRole ARN>
   aws cloudformation describe-change-set --stack-name audigo-ai-api \
     --change-set-name import-ai-service \
     --query 'Changes[].ResourceChange.[Action,LogicalResourceId]' --output table
   ```
   `Import AudigoAiService` 1건만 나와야 합니다. 식별자 이름은 `get-template-summary`로 확인할 수 있습니다.
5. **실행 후 확인.** `IMPORT_COMPLETE`, 서비스가 계속 `audigo-mock-ai:8`로 1개 실행 중인지 확인합니다.
6. **PR 병합.** CD 계획에서 `audigo-ai-api`가 변경 없음으로 나와야 합니다. IMPORT 전에 병합하면 스택이 없어 계획이 실패합니다.
7. **드리프트 확인.** `scripts/drift-check.sh`를 다시 실행합니다.

## 확인이 필요한 사항

- **배포 설정과 `ai-deploy.yml` 흐름이 맞지 않을 수 있습니다.**
  - 현재 설정은 최대 100%, 최소 0%입니다. 태스크가 1개일 때 ECS는 기존 Worker를 먼저 멈춘 뒤 새 Worker를 띄웁니다.
  - 반면 `ai-deploy.yml`은 새 Worker를 먼저 띄우고, 기존 Worker를 Task Protection으로 보호한 채 드레이닝합니다.
  - 이번 IMPORT에서는 현재 값을 그대로 옮겼습니다. 최대 200% 등으로 바꿀지는 EC2 인스턴스 여유 용량과 함께 별도 PR에서 검토합니다.
- `ai-deploy.yml`에 배포 실패 시 신규 리비전 deregister 단계 추가 (워크플로 파일, 별도 작업).
- 서비스 이름이 `mock-ai`입니다. 실제 AI Worker로 교체할 때 서비스 이름이 바뀌면 이 스택과 IAM ARN도 함께 바꿔야 합니다.
