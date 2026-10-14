# WebSocket Task Definition 편입

## 범위 및 현재 확인된 상태

2026-10-14 사용자 제공 AWS CLI 결과로 기존 Task Definition 편입 완료를 확인했습니다.
아래 절차는 작업 기록이며, 편입 완료가 WebSocket 서버 실행까지 의미하지는 않습니다.

| 항목 | 값 또는 확인 결과 |
|---|---|
| 브랜치 / 대상 스택 | `feat/websocket` / `audigo-websocket` |
| 템플릿 | `cloudformation/websocket.yaml` |
| 논리적 ID | `AudigoWebsocketTaskDefinition` |
| 기존 Task Definition | `audigo-websocket-Fargate:1`, ACTIVE |
| 편입 전 스택 소유 조회 | 서울 리전 DescribeStackResources에서 해당 ARN의 스택 없음 |
| 편입 후 스택 상태 | `audigo-websocket`: `IMPORT_COMPLETE` |
| 편입 후 관리 리소스 | Task Definition 1개, 리소스 상태 `UPDATE_COMPLETE` |
| 기존 물리적 ID 유지 | `arn:aws:ecs:ap-northeast-2:185489190599:task-definition/audigo-websocket-Fargate:1` 확인 |
| Service | `audigo-ecs-cluster`에서 해당 Family를 사용하는 서비스 조회 결과 없음 |
| 실행 설정 | Fargate / awsvpc / ARM64 / LINUX / 0.5 vCPU / 2 GiB |
| 컨테이너 | `spring-websocket`, CPU 512 / 메모리 2048 / 예약 메모리 1536 |
| 포트 / 헬스 체크 | TCP 8081 / `http://127.0.0.1:8081/actuator/health` |
| 외부 참조 | 기존 실행 역할, ECR 이미지 digest, Secret ARN, 로그 그룹 |

이번 편입 대상은 Task Definition 한 개입니다. Service, 실행 중인 Task,
ScalableTarget, Scaling Policy, Task Role, ALB 규칙을 생성하거나 편입하지 않습니다.
서비스 미조회는 해당 클러스터에서 해당 Family를 사용하는 서비스에 한정된 결과입니다.
다른 클러스터 또는 단독 RunTask 실행 여부까지 확인한 것은 아닙니다.

기존 환경변수의 DB 주소, AI 주소 `http://10.0.15.32:8000`, `JPA_DDL_AUTO=update`,
컨테이너 이미지 및 Secret의 JSON key 참조를 그대로 보존합니다.
포트 매핑 이름 `websocket-8080-tcp`는 실제 포트 8081과 다르지만 편입 중 변경하지 않습니다.
실제 Secret 값은 코드에 넣지 않습니다. Outputs와 신규 태그도 이번에는 추가하지 않습니다.

## 1. 작업 전 백업 및 최신 상태 확인

CloudShell에서 아래 조회 결과를 안전하게 보관합니다. 환경변수에 비밀 값이 있다면
공유하거나 Git에 커밋하지 않습니다. 백업 JSON은 API 조회 결과이며, 템플릿 자체가 아닙니다.

```bash
aws ecs describe-task-definition \
  --region ap-northeast-2 \
  --task-definition audigo-websocket-Fargate:1 \
  --include TAGS --output json --no-cli-pager \
  > audigo-websocket-task-v1.backup.json

aws cloudformation describe-stack-resources \
  --region ap-northeast-2 \
  --physical-resource-id arn:aws:ecs:ap-northeast-2:185489190599:task-definition/audigo-websocket-Fargate:1 \
  --output json --no-cli-pager
```

작업 시점의 정의와 템플릿이 일치하는지 확인합니다.
다른 스택에서 관리 중이거나 설정이 변경됐다면 해당 상태를 확인한 뒤 진행합니다.

## 2. 기존 리소스 Import로 스택 생성

서울 리전 CloudFormation에서 **스택 생성 → 기존 리소스 사용(리소스 가져오기)**를 선택합니다.

- 스택 이름: `audigo-websocket`
- 업로드할 전체 템플릿: `cloudformation/websocket.yaml`
- 논리적 ID: `AudigoWebsocketTaskDefinition`
- 리소스 유형: `AWS::ECS::TaskDefinition`
- 기존 리소스 ARN:

```text
arn:aws:ecs:ap-northeast-2:185489190599:task-definition/audigo-websocket-Fargate:1
```

식별자 속성은 콘솔에 표시되는 항목을 사용합니다. CloudShell에 템플릿을 업로드했다면
다음 조회로 식별자 속성을 확인할 수 있습니다. 파일 경로는 업로드 위치에 맞춥니다.

```bash
aws cloudformation get-template-summary \
  --region ap-northeast-2 \
  --template-body file://websocket.yaml \
  --query ResourceIdentifierSummaries --output json --no-cli-pager
```

`AudigoWebsocketTaskDefinition` 한 개의 **Import만** 있는 변경 세트를 확인합니다.
일반 CREATE/UPDATE, 리소스 교체 또는 삭제가 보이면 실행하지 않습니다.
Task Definition 리비전은 Import 식별자로 연결하며, 템플릿 속성에 `Revision: 1`을 추가하지 않습니다.
일반 생성으로 진행하면 기존 `:1` 편입이 아니라 새 리비전 등록이 될 수 있습니다.
기존 서비스 스택을 삭제하는 REST 이관 절차를 여기에 그대로 적용하지 않습니다.

## 3. 편입 결과 검증

```bash
aws cloudformation describe-stacks \
  --region ap-northeast-2 --stack-name audigo-websocket \
  --query 'Stacks[0].StackStatus' --output text --no-cli-pager

aws cloudformation list-stack-resources \
  --region ap-northeast-2 --stack-name audigo-websocket \
  --query 'StackResourceSummaries[].{LogicalId:LogicalResourceId,PhysicalId:PhysicalResourceId,Status:ResourceStatus}' \
  --output table --no-cli-pager
```

- 스택 `IMPORT_COMPLETE` 확인.
- 관리 대상 한 개와 기존 ARN의 `:1` 유지 확인.
- 이벤트의 태그 처리 후 리소스 `UPDATE_COMPLETE`가 보일 수 있음.
- 편입 결과와 별도로 Drift Detection을 통해 선언한 설정을 확인.
- 서버 실행·8081 리스닝·WebSocket 접속 정상 여부는 이번 편입 성공만으로 입증되지 않음.

위 스택 상태와 리소스 ID는 2026-10-14 사용자 제공 조회 결과로 확인했습니다.
Drift Detection, 서버 실행 및 접속 검증 결과는 아직 제공되지 않았습니다.

실패하면 이벤트의 구체적인 오류와 남은 리소스를 조회합니다.
오류 해결을 위해 Task Definition을 등록 해제하거나 스택을 무작정 삭제하지 않습니다.

## 4. Git / CI 및 후속 구성

- YAML과 이 문서, 회귀 테스트를 `feat/websocket` PR로 검증합니다.
- `deploy/stacks.json`의 WebSocket은 `enabled: false`, `allow_create: false`, 빈 배포 역할을 유지합니다.
- CI는 비활성 스택의 작성된 템플릿도 검증합니다. 배포 IAM 구성 전에는 CD를 활성화하지 않습니다.
- 신규 Service 생성은 이후 별도 작업입니다. 서브넷, 보안 그룹, ALB 대상 그룹과 경로,
  애플리케이션이 8081에서 실제로 실행되는지 먼저 확인해야 합니다.
- 공통 역할·Secret·ECR·로그 그룹의 관리와 신규 Task Role·Scaling Policy·Outputs는 별도 작업입니다.

## 공식 문서

- [기존 리소스로 스택 생성](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/resource-import-new-stack.html)
- [Task Definition 리소스 및 리비전](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ecs-taskdefinition.html)
- [템플릿 식별자 조회](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/get-template-summary.html)
