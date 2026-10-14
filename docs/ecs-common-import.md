# 기존 ECS 클러스터 편입

## 현재 상태와 범위

2026-10-14 사용자가 제공한 `describe-clusters`와 현재 스택 전체의 ECS 리소스 목록을 기준으로 작성했습니다.
두 클러스터는 실제로 존재하며 `ACTIVE`입니다. 제공된 서울 리전 스택 목록에는
`AWS::ECS::Cluster` 또는 `AWS::ECS::ClusterCapacityProviderAssociations` 선언이 없습니다.
스캔 원본에 클러스터 코드가 있다고 실제 스택이 관리하는 것은 아닙니다.

이번 작업은 로컬 템플릿 작성이며 **AWS Import는 아직 실행하지 않았습니다.**
[ecs-common.yaml](../cloudformation/ecs-common.yaml)에는 기존 클러스터 2개만 선언합니다.
공통 ECS Task Execution Role, 서비스, Task Definition, 등록 EC2, 배포용 IAM 역할은 추가하지 않습니다.

| 논리 ID | 리소스 유형 | 식별자 속성 | 기존 식별자 값 |
|---|---|---|---|
| `AudigoEcsCluster` | `AWS::ECS::Cluster` | `ClusterName` | `audigo-ecs-cluster` |
| `AudigoStagingEcsCluster` | `AWS::ECS::Cluster` | `ClusterName` | `audigo-staging-ecs-cluster` |

실제 클러스터 ARN:

```text
arn:aws:ecs:ap-northeast-2:185489190599:cluster/audigo-ecs-cluster
arn:aws:ecs:ap-northeast-2:185489190599:cluster/audigo-staging-ecs-cluster
```

## 유지하는 설정

| 항목 | 운영 | 스테이징 |
|---|---|---|
| ClusterName | `audigo-ecs-cluster` | `audigo-staging-ecs-cluster` |
| CapacityProviders | `FARGATE`, `FARGATE_SPOT` | 동일 |
| DefaultCapacityProviderStrategy | 빈 배열 `[]` | 동일 |
| containerInsights | `disabled` | 동일 |
| ECS Exec Logging | `DEFAULT` | 동일 |
| 태그 | `name=ecs-on-ec2`, `version=v1` | 없음 |

- 소문자 `name` 태그 키를 유지하며, 기존 리소스 이름을 변경하지 않습니다.
- Capacity Provider 연결은 Cluster 속성으로 관리하고 별도 Association 리소스로 중복 관리하지 않습니다.
- `FARGATE`와 `FARGATE_SPOT`를 새로운 `AWS::ECS::CapacityProvider`로 생성하지 않습니다.
- 빈 기본 전략을 임의의 Fargate 전략으로 바꾸지 않습니다. 이 편입은 EC2 → Fargate 전환이 아닙니다.
- 서비스 수, 등록 EC2 수, 실행 태스크 수는 관측값입니다. 클러스터 선언에 넣지 않습니다.
- 보존 정책은 모두 `Retain`입니다. 잘못된 속성 업데이트까지 막아주는 것은 아닙니다.

## 편입 절차

1. GitHub CI에서 전체 검증과 테스트 통과를 확인합니다. 작업 직전 기존 설정과 소유 스택을 다시 확인합니다.
2. CloudFormation 서울 리전에서 새 스택의 **기존 리소스 가져오기**를 선택합니다.
3. `cloudformation/ecs-common.yaml`을 업로드하고 스택 이름은 `audigo-ecs-common`으로 지정합니다.
4. 위 논리 ID에 해당 기존 클러스터를 연결합니다. 식별자 속성·형식은 콘솔 안내 또는
   아래 명령의 `ResourceIdentifierSummaries`에서 확인합니다. 이름과 ARN을 임의로 바꿔 넣지 않습니다.
5. 변경 세트에 기존 클러스터 **Import 2개만** 있는지 확인합니다. 서비스 생성·수정·삭제나 교체가 있으면 실행하지 않습니다.
6. Import 완료 후 클러스터 ARN 유지와 스택 관리 목록을 확인합니다.
7. Drift Detection 지원 속성과 실제 클러스터 설정을 비교합니다. 서비스·태스크 상태도 따로 확인합니다.

식별자 확인용 조회:

```bash
aws cloudformation get-template-summary \
  --region ap-northeast-2 \
  --template-body file://ecs-common.yaml \
  --query 'ResourceIdentifierSummaries' \
  --output json \
  --no-cli-pager
```

이 명령은 최신 `ecs-common.yaml`을 CloudShell에 업로드하고 해당 디렉터리에서 실행합니다.
저장소 루트에서는 `file://cloudformation/ecs-common.yaml`을 사용합니다.
Import에 필요한 사용자 권한과 실행 권한은 기존 수동 편입 경로에서 확인합니다.

신규 Outputs는 넣지 않았습니다. 전체 편입 후 별도 UPDATE로 추가합니다.
배포 설정은 `enabled: false`, `allow_create: false`, `role_arn: ""`로 유지합니다.
배포용 IAM의 IaC 관리와 최소 권한 확장은 기존 리소스 편입 후 진행합니다.

## 기존 서비스는 그대로 유지

정상 완료 상태의 서비스 5개는 기존 ECS 콘솔 스택에서 관리 중입니다.
AI와 Mock AI는 스택 리소스 상태가 `CREATE_FAILED`지만 물리 ARN이 기록되어 있습니다.
실제 서비스 상태·소유 관계·스택 이벤트를 확인하기 전에는 재편입하거나 스택을 삭제하지 않습니다.
클러스터 편입으로 이 서비스들이 새 스택으로 옮겨지지는 않습니다.

- [현재 편입 현황과 기존 ECS 서비스 목록](import-progress.md)
- [AWS::ECS::Cluster 속성](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ecs-cluster.html)
- [CloudFormation Import 지원 목록](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/resource-import-supported-resources.html)
- [GetTemplateSummary의 식별자 정보](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/get-template-summary.html)

## 후속 작업 구분

2026-10-14 사용자 결정: SQS, DB(RDS), Cache(Valkey), 모니터링은 기존 편입이 아닌 신규 생성 대상으로 진행합니다.
각각 `feat/sqs`, `feat/db`, `feat/cache`, `feat/monitoring` 브랜치에서 설계 확정 후 작성합니다.
이번 `feat/ecs-common`에는 해당 리소스 생성이나 배포 권한 변경을 포함하지 않습니다.
REST·AI·WebSocket 서비스는 기존 스택 소유 여부와 현재 구성을 확인한 뒤 관리 이전 또는 신규 생성 범위를 결정합니다.
