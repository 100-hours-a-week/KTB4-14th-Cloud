# REST API 기존 리소스 편입 기록 및 절차

## 현재 상태

2026-10-14 사용자 제공 CLI 및 편입 완료 보고를 기준으로 작성했습니다.
Task Definition, Service, ScalableTarget의 `audigo-rest-api` 편입 완료가 보고됐으며,
최종 JSON을 `cloudformation/rest-api.yaml`에 동기화했습니다.
최종 스택 상태·물리 ID·드리프트·ALB/API 정상 여부는 아래 절차로 별도 확인합니다.
REST CD는 비활성 상태로 유지합니다. 아래 이전 절차를 반복 실행하지 않습니다.

| 대상 | 확인된 상태 |
|---|---|
| 대상 스택 / 브랜치 | `audigo-rest-api` / `feat/rest-api` |
| 이전 서비스 소유 스택 | `ECS-Console-V2-Service-audigo-backend-Fargate-service-audigo-ecs-cluster-2085cccf` |
| 현재 관리 대상 | `AudigoRestTaskDefinition`, `AudigoRestService`, `AudigoRestScalableTarget` (편입 완료 보고) |
| 서비스 | `audigo-backend-Fargate-service`, Fargate, Desired 1 / Running 1 / Pending 0 |
| 실제 Task Definition | `audigo-backend-Fargate:5` |
| 기존 스택 템플릿 | Task Definition `:2`, ALB 연결 선언 없음, Service 하나만 관리 |
| Scalable Target | 최소 1 / 최대 2, 기존 스택 목록에 관리 대상 없음 확인 후 편입 완료 보고 |
| Scaling Policy / 예약 확장 | 모두 없음 |

이미지 digest, DB/AI 주소, Secret ARN, 네트워크, ALB 연결, 기존 역할을 유지합니다.
`JPA_DDL_AUTO=update`, EC2 DB/AI 주소, Public IP 활성화는 현재 상태 보존이며 V2 권장 설계가 아닙니다.
Task Role, Scaling Policy, 로그 그룹, Secret, ECR, 공통 역할은 이번에 생성/편입하지 않습니다.
REST CD는 disabled / allow_create false로 유지하고 새 Outputs는 넣지 않습니다.
작업 기간에는 백엔드 애플리케이션 배포와 수동 ECS 변경을 중지하여 리비전 경쟁을 방지합니다.

## 단계별 파일

| 단계 | 템플릿 | 관리 리소스 | 실행 전제 |
|---|---|---|---|
| 1 | `deploy/imports/rest-api-task.json` | Task Definition 1개 | `:5` 설정과 소유 여부 재확인 |
| 2 | `deploy/imports/rest-api-service.json` | 기존 Task Definition + Service | 서비스 보존 및 기존 스택 관리 해제 확인 |
| 3 | `deploy/imports/rest-api-scalable-target.json` | 위 2개 + Scalable Target | 대상 소유 스택 없음 확인 |
| 최종 관리 | `cloudformation/rest-api.yaml` | 편입한 리소스 3개 모두 | 최종 JSON과 동기화, CD는 비활성 유지 |

JSON은 단계별 전체 템플릿입니다. 일반 CREATE/UPDATE가 아니라 Import에 사용합니다.
각 단계의 변경 세트는 **Import 1개뿐**이어야 합니다. 이미 편입한 리소스는 변경하지 않습니다.
Service의 Ref는 편입된 Task Definition 실제 ARN `:5`로 해석되어야 합니다.

Service의 `PropagateTags`는 생략합니다. ECS 조회 결과는 `NONE`이지만,
CloudFormation에서 명시 가능한 값은 `SERVICE`, `TASK_DEFINITION`뿐입니다.
생략하면 기본값 `NONE`을 유지합니다. `EnableECSManagedTags: true`는 그대로 유지합니다.
`#/PropagateTags` enum 검증 실패로 Import가 롤백되면 실제 서비스와 스택 리소스를
재조회한 뒤, 수정된 전체 템플릿으로 Service 1개만 다시 Import합니다.
이 오류를 해결하기 위해 스택이나 서비스를 삭제하거나 일반 UPDATE하지 않습니다.

각 단계 완료 후 해당 JSON을 YAML로 변환하여 `cloudformation/rest-api.yaml`을 같은 단계로 갱신하고 Git에 기록합니다.
현재 YAML은 **3단계까지 완료한 최종 내용**이며, 과거 1단계 템플릿으로 사용하지 않습니다.
**1단계 YAML을 2/3단계 스택에 다시 UPDATE하면 Service/Target 제거 계획이 생길 수 있습니다.**
REST CD 활성화는 최종 단계의 AWS와 Git 코드가 일치한 뒤 별도 진행합니다.
Retain은 잘못된 속성 업데이트나 재배포를 막아주지 않습니다.

## 0. 작업 전 백업과 조회

같은 CloudShell 터미널에서 조회하고 결과를 안전하게 보관합니다. 실제 비밀 값은 조회/커밋하지 않습니다.

```bash
STACK='ECS-Console-V2-Service-audigo-backend-Fargate-service-audigo-ecs-cluster-2085cccf'
mkdir -p rest-api-backup
aws cloudformation get-template --region ap-northeast-2 --stack-name "$STACK" \
  --template-stage Original --query TemplateBody --output json --no-cli-pager \
  > rest-api-backup/service-stack-template.json
aws cloudformation describe-stacks --region ap-northeast-2 --stack-name "$STACK" \
  --output json --no-cli-pager > rest-api-backup/service-stack.json
aws cloudformation list-stack-resources --region ap-northeast-2 --stack-name "$STACK" \
  --output json --no-cli-pager > rest-api-backup/service-stack-resources.json
aws ecs describe-services --region ap-northeast-2 --cluster audigo-ecs-cluster \
  --services audigo-backend-Fargate-service --include TAGS --output json --no-cli-pager \
  > rest-api-backup/service.json
aws ecs describe-task-definition --region ap-northeast-2 \
  --task-definition audigo-backend-Fargate:5 --include TAGS --output json --no-cli-pager \
  > rest-api-backup/task-definition.json
aws elbv2 describe-target-health --region ap-northeast-2 \
  --target-group-arn arn:aws:elasticloadbalancing:ap-northeast-2:185489190599:targetgroup/audigo-v1-Fargate/53f2677044a1644f \
  --output json --no-cli-pager > rest-api-backup/target-health.json
```

기존 Outputs가 Export되어 있다면 이용 스택을 확인하고 참조 해소 전에는 삭제하지 않습니다.
서비스 ARN/리비전 변경이나 기존 스택의 리소스 추가가 있으면 중지하고 절차를 다시 검토합니다.
백업 당시가 아니라 작업 직전의 실제 상태와 템플릿이 일치해야 합니다.

## 1. Task Definition :5만 Import

CloudFormation의 기존 리소스 가져오기로 새 `audigo-rest-api` 스택을 준비합니다.
1단계 템플릿만 업로드하고 `AudigoRestTaskDefinition`에 아래 실제 ARN을 연결합니다.

```text
arn:aws:ecs:ap-northeast-2:185489190599:task-definition/audigo-backend-Fargate:5
```

식별자 속성은 콘솔 또는 다음 조회로 확인합니다. 필드 이름을 추측하지 않습니다.
CloudShell에 업로드한 파일의 실제 경로에서 실행하세요.

```bash
aws cloudformation get-template-summary --region ap-northeast-2 \
  --template-body file://deploy/imports/rest-api-task.json \
  --query ResourceIdentifierSummaries --output json --no-cli-pager
```

변경 세트는 Task Definition 1개 Import만 있어야 합니다. CREATE/UPDATE/DELETE/Replacement는 허용하지 않습니다.
`IMPORT_COMPLETE`, Physical ID가 기존 `:5`인지, 기존 서비스도 계속 `:5`를 사용하는지 확인합니다.
새 리비전을 등록하는 작업이 아니며, 기존 서비스는 원래 스택에서 계속 관리합니다.

## 2. 기존 스택에 Retain만 적용 — 운영 변경 별도 검토

백업한 원본 스택 템플릿을 복제하고 기존 논리ID `ECSService`에
`DeletionPolicy: Retain`, `UpdateReplacePolicy: Retain`만 추가합니다.
기존 Parameters, Outputs, Properties는 변경하지 않습니다. `:2`→`:5` 변경이나 ALB 추가와 섞지 않습니다.
기존 스택의 실제 파라미터 값도 UsePreviousValue 등을 통해 유지합니다.

CloudFormation 변경 세트와 템플릿 차이를 검토하여 보호 정책 이외의 변경이 없는지 확인합니다.
TaskDefinition, LoadBalancers, DesiredCount, 네트워크 속성 변경 또는 Replacement가 있으면 실행하지 않습니다.
오래된 `:2`를 운영에 재적용하지 않도록 주의합니다. 정책만 변경하더라도 무중단을 보장하지 않습니다.
업데이트 후 서비스 `:5`, Running count, ALB 대상 상태를 다시 확인합니다.
기존 스택 `UPDATE_COMPLETE`와 **AWS에 저장된 템플릿의 두 Retain 설정**을 조회해 증거를 보관합니다.
로컬 파일에 정책을 추가했다는 사실만으로는 다음 단계로 넘어가지 않습니다.

## 3. 기존 스택 관리 해제 — 삭제 작업 별도 확인

기존 스택은 서비스 한 개뿐이므로 빈 Resources로 UPDATE하지 않습니다.
Retain이 실제 적용된 기존 스택을 삭제하는 방식을 사용합니다.
**이 준비 문서는 스택 삭제 실행 승인이나 즉시 실행 지시가 아닙니다.**

Task Definition 편입 성공, 기존 Retain 적용 확인, Outputs 참조 확인,
새 Service 편입 템플릿·권한·식별자 준비가 완료된 뒤 운영 작업을 별도로 판단합니다.
삭제 이벤트의 `ECSService: DELETE_SKIPPED`, 스택 `DELETE_COMPLETE`를 확인하고,
실제 서비스 ARN·ACTIVE·Running count·`:5`·ALB 연결 유지 여부를 확인합니다.
Service 삭제 API, DesiredCount=0, 강제 삭제, Task Definition deregister는 실행하지 않습니다.
관리 해제부터 재편입까지의 시간을 최소화하고 다른 배포를 차단합니다.

## 4. 기존 audigo-rest-api에 Service Import

2단계 템플릿을 사용합니다. 기존 Task Definition 선언은 1단계와 동일합니다.
`AudigoRestService`를 다음 실제 서비스 ARN과 연결합니다.

```text
arn:aws:ecs:ap-northeast-2:185489190599:service/audigo-ecs-cluster/audigo-backend-Fargate-service
```

Service 1개 Import만 있어야 합니다. 원래 스택 소유 상태에서 중복 Import하지 않습니다.
완료 후 기존 ARN·`:5`·Desired/Running·ALB 대상 상태·실제 HTTPS API 동작을 비교합니다.
실패하면 이벤트와 실제 서비스 상태부터 확인하고 삭제/재생성하지 않습니다.
관리 미소속이지만 서비스가 실행 중이라면 설정을 유지하면서 원인을 해결하고 재Import를 검토합니다.
기존 `:2` 템플릿을 CREATE해서 복구하려 하거나 새 스택을 무작정 삭제하지 않습니다.

## 5. Scalable Target은 소유 확인 후 별도 Import

기존 서비스 스택 목록에는 없지만 다른 스택 소유 가능성은 아직 확인하지 않았습니다.

```bash
aws cloudformation describe-stack-resources --region ap-northeast-2 \
  --physical-resource-id arn:aws:application-autoscaling:ap-northeast-2:185489190599:scalable-target/0ec5774ccc7ebb454f719911f52aa608ed5b \
  --output json --no-cli-pager
```

물리 식별자나 소유 여부가 불분명하면 활성 스택의 ScalableTarget 목록을 조회하여 ResourceId와 대조합니다.
미소속 및 현재 설정 일치를 확인한 뒤 3단계 템플릿으로 `AudigoRestScalableTarget`만 Import합니다.
식별자 속성은 콘솔/get-template-summary에서 확인합니다.
최소 1/최대 2, SuspendedState false, 기존 서비스 연결 역할을 유지합니다.
정책이나 예약 확장이 새로 추가됐다면 중지하고 재확인합니다.

## 6. 완료 검증과 후속 작업

- 스택 Import 성공, 3개 리소스의 ID 유지, 서비스 `:5`, ALB/API 정상 확인.
- Drift Detection으로 선언한 설정을 비교. Drift만으로 애플리케이션 정상 여부를 판단하지 않음.
- 완료 단계 템플릿을 `cloudformation/rest-api.yaml`에 동기화하고 PR/CI에 기록.
- 신규 Task Role·Scaling Policy·Outputs는 후속 독립 UPDATE로 진행.
- 공통 역할·Secret·ECR·로그 그룹은 별도 관리 범위 확인.
- 배포 IAM 준비 완료 전 REST CD 비활성화 유지.

## 검증 명령어

일반 CI는 등록된 YAML과 테스트를 검증합니다. cfn-lint가 설치된 CI에서는 테스트가 단계별 JSON 3개도 lint합니다.
단계별 JSON은 배포 설정에는 등록하지 않습니다. 실제 작업 직전에도 아래 명령으로 재검증합니다.

```bash
python3 -m unittest discover -s tests -v
python3 scripts/infra.py validate --config deploy/stacks.json --lint
cfn-lint -t deploy/imports/rest-api-task.json deploy/imports/rest-api-service.json deploy/imports/rest-api-scalable-target.json
```

## 공식 자료

- [Import 지원 목록](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/resource-import-supported-resources.html)
- [스택 간 관리 이전과 Retain](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/refactor-stacks.html)
- [ECS Service](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ecs-service.html)
- [ECS Task Definition](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ecs-taskdefinition.html)
