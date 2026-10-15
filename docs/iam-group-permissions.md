# CloudFormation 그룹별 IAM 권한 운영 가이드

## 목적과 범위

`bootstrap/infra-iam.yaml`은 Audigo CloudFormation 배포를 다음 **4개 역할**으로 분리한다.

| 역할 | 책임 | 현재 권한 기준 |
| --- | --- | --- |
| `AudigoGitHubInfraDeployRole` | GitHub Actions OIDC 인증, Change Set 생성·조회·실행, 실행 역할 전달 | 기존 7개 스택과 신규 audigo-db |
| `AudigoCfnCoreExecutionRole` | 네트워크와 DNS | `network.yaml`, `dns.yaml` |
| `AudigoCfnPlatformExecutionRole` | ECS 공통 클러스터, ALB, 신규 RDS | `ecs-common.yaml`, `alb.yaml`, `db.yaml` |
| `AudigoCfnAppExecutionRole` | REST API, WebSocket Task Definition, Frontend | `rest-api.yaml`, `websocket.yaml`, `frontend.yaml` |

이 문서는 **현재 편입·검토한 리소스와 신규 DB**를 다룬다. ECR, Cache, SQS, AI API, ACM, 보안 그룹 변경, WAF, DNS 레코드, 모니터링은 이 권한 세트에 포함하지 않는다. RDS 관리형 Secret 생성·태그 및 AWS 관리형 암호화 키 사용만 Platform에 추가하며 Secret 값 조회는 허용하지 않는다.

> `bootstrap/infra-iam.yaml`은 `cloudformation/` 하위가 아니다. 일반 인프라 CD의 대상에서 제외하고, 별도 관리자 검토·승인으로만 적용한다. GitHub 인프라 배포 역할이 자신의 권한이나 OIDC 신뢰 정책을 수정할 수 없어야 한다.

## 권한 경계

```text
GitHub Actions (OIDC)
  └─ AudigoGitHubInfraDeployRole
       ├─ plan subject: CreateChangeSet + 조회 + 지정 실행 역할 PassRole
       └─ production subject: ExecuteChangeSet + 조회
            └─ CloudFormation
                 ├─ AudigoCfnCoreExecutionRole
                 ├─ AudigoCfnPlatformExecutionRole
                 └─ AudigoCfnAppExecutionRole
```

- OIDC 신뢰 정책은 `aud=sts.amazonaws.com`과 **실제 `sub` 문자열**을 동시에 검사한다.
- `PlanOidcSubject`, `ProductionOidcSubject`에는 GitHub Actions에서 실제 발급된 OIDC 토큰의 `sub`를 넣는다. 템플릿은 `100-hours-a-week/KTB4-14th-Cloud` 및 각 Environment 이름을 정규식으로 검증하고, immutable ID 형식도 허용한다. 두 subject는 CloudFormation Rules로 서로 달라야 한다.
- Plan 세션은 `cloudformation:ExecuteChangeSet`을 갖지 않는다.
- Production 세션은 `cloudformation:CreateChangeSet` 및 `iam:PassRole`을 갖지 않는다.
- CreateChangeSet은 Core / Platform / App 실행 역할 ARN만 `cloudformation:RoleArn`으로 지정할 수 있다.
- ExecuteChangeSet은 AWS의 `cloudformation:RoleArn` 조건 키를 지원하지 않으므로, 생성 단계의 RoleArn 조건과 `scripts/infra.py`의 Change Set marker·대상 스택 검증을 함께 사용한다.
- 실행 역할의 신뢰 주체는 `cloudformation.amazonaws.com`뿐이다. GitHub Actions나 IAM 사용자가 직접 AssumeRole할 수 없다.
- Environment subject에는 branch ref가 포함되지 않으므로 GitHub `iac-plan`과 `iac-production` Environment의 **Deployment branches를 main으로 제한**해야 한다. 이것은 OIDC trust의 저장소 제한과 함께 필수 운영 통제다.

## 스택 그룹과 현재 관리 범위

| 그룹 | 스택 | 현재 관리 리소스 | 의도적으로 제외한 리소스 |
| --- | --- | --- | --- |
| Core | `audigo-network` | VPC, 기존 Public Subnet 2개, IGW, Route Table 2개, S3 Gateway Endpoint, 관련 Route/Association | 신규 Private Subnet, NAT Gateway, Security Group |
| Core | `audigo-dns` | `audigo.kr` Hosted Zone 메타데이터·태그 | 모든 DNS RecordSet, 도메인 등록 기관 |
| Platform | `audigo-ecs-common` | 운영·스테이징 ECS Cluster 2개 | ECS Service, ECS Task Definition, IAM 역할 |
| Platform | `audigo-alb` | 기존 ALB, HTTPS Listener, Fargate Target Group | Security Group, ACM 인증서, ECS Target 등록 |
| Platform | `audigo-db` | 신규 private RDS MySQL, DB Subnet Group, DB Parameter Group | 기존 VPC·Subnet·SG 변경, Read Replica, CMK 생성, 앱/CDC 계정 |
| App | `audigo-rest-api` | 기존 REST Task Definition, Fargate Service, 기존 Scalable Target | 새 Scaling Policy, Task Role, ECR/Secret/Log 리소스 |
| App | `audigo-websocket` | 기존 WebSocket Task Definition | WebSocket Service, Auto Scaling, ALB Listener Rule |
| App | `audigo-frontend` | 기존 S3 Bucket/Policy, CloudFront OAC/Distribution | S3 객체, ACM 인증서, WAF, Cache Policy, Route 53 RecordSet |

`deploy/stacks.json`의 `enabled`는 배포 시점 제어이고, IAM 정책의 허용은 보안 경계다. 따라서 기존 7개와 신규 DB 스택에 필요한 정책을 bootstrap에 정의해도, 스택은 **한 번에 활성화하지 않는다.** 역할 ARN 연결과 Change Set을 확인한 후 스택별 PR/CD 검증을 진행한다.

## `Resource: "*"` 사용 근거

`AdministratorAccess`, `PowerUserAccess`, `ec2:*`, `ecs:*` 같은 광범위 권한은 사용하지 않는다. 아래 `Resource: "*"`는 AWS 서비스 권한 모델에서 해당 조회 또는 OAC API에 리소스 ARN 제한을 제공하지 않는 경우에만 사용한다.

| 역할 | API | 이유 | 추가 제한 |
| --- | --- | --- | --- |
| Core | EC2 `Describe*` | EC2 일부 Describe API는 리소스 수준 권한을 지원하지 않음 | `aws:RequestedRegion` = 배포 리전 |
| Platform | ELBv2 `Describe*` | 해당 조회 API는 리소스 ARN 제한을 지원하지 않음 | `aws:RequestedRegion` = 배포 리전 |
| Platform | RDS engine/default parameter 조회, EC2 네트워크 조회 | 해당 조회 API는 리소스 ARN 제한을 제공하지 않음 | `aws:RequestedRegion` = 배포 리전 |
| App | ECS `DescribeTaskDefinition`, Auto Scaling `DescribeScalableTargets` | 해당 API는 리소스 수준 권한을 지원하지 않음 | `aws:RequestedRegion` = 배포 리전 |
| App | CloudFront OAC 조회·수정 | OAC API의 리소스 수준 권한 모델 제한 | OAC 생성·삭제 권한 없음 |

ECS Cluster/Service/Task Definition/Scalable Target 조회도 관리하는 ARN으로 제한한다. 기존 리소스의 신규 생성·교체·삭제는 기본 정책에 포함하지 않는다. 신규 DB의 생성·수정·롤백 삭제 API는 `audigo-prd-db` 및 두 그룹의 정확한 ARN에 추가한다. DB 스택은 `Retain`과 삭제 보호를 함께 사용한다. 추가 작업 전 Change Set과 IAM Policy Simulator/Access Analyzer 결과를 검토하고 필요한 action·ARN만 별도 PR로 추가한다.

## 런타임 권한과 배포 권한의 분리

`ExecutionRoleArn`은 ECS 태스크 실행 역할이며 CloudFormation 실행 역할과 다르다.

- ECR image pull, Secrets Manager 값 조회, CloudWatch Logs 전송은 ECS 태스크의 execution role이 담당한다.
- 그러므로 이 bootstrap 정책에는 `ecr:*`, `secretsmanager:GetSecretValue`, CloudWatch Logs 쓰기 권한을 넣지 않는다.
- App 실행 역할에는 템플릿이 참조하는 기존 `audigo-ecs-task-execution-role`을 `ecs-tasks.amazonaws.com`에 전달하는 `iam:PassRole`만 있다.
- 기존 REST Scalable Target가 참조하는 Application Auto Scaling service-linked role도 `application-autoscaling.amazonaws.com`에만 전달할 수 있다.
- 실행 역할에는 IAM 역할 생성·수정·삭제, GitHub OIDC 공급자 변경, bootstrap 역할 4개에 대한 PassRole 권한이 없다.

## 초기 적용 절차

1. `bootstrap/infra-iam.yaml`을 PR에서 cfn-lint·정책 테스트한다. `CAPABILITY_NAMED_IAM`이 필요하다. 로컬 문법 테스트만으로는 IAM 정책 유효성을 보장하지 않으므로, 실제 적용 전 Policy Simulator/Access Analyzer 검토가 필수다.
2. 관리자 권한으로 `audigo-infra-iam` 같은 **별도 bootstrap 스택** Change Set을 만든다. 일반 `infra_batch.py` 또는 GitHub 배포 역할로 실행하지 않는다.
3. Change Set에서 생성 대상이 역할 4개뿐인지, 정책에 ECR·모니터링·IAM 관리 권한이 없는지 확인한다.
4. AWS IAM Policy Simulator와 IAM Access Analyzer로 각 역할의 허용/거부를 검토한다. 특히 GitHub 역할의 plan subject가 ExecuteChangeSet을 거부하고 production subject가 CreateChangeSet/PassRole을 거부하는지 확인한다.
5. 적용 후 Outputs의 역할 ARN을 GitHub Environments/Repository Variables에 설정하고, `stacks.json`의 그룹 매핑과 일치하는지 확인한다.
6. 기존 스택 Service Role 연결은 별도 Change Set으로 스택별 검토한다. CloudFormation Service Role은 연결 후 제거할 수 없고, 스택 조작 권한이 있는 다른 주체도 그 역할을 사용할 수 있으므로 기존 권한을 함께 감사한다.
7. 새 역할로 Network부터 Change Set 생성·승인·실행을 검증한다. 이후 한 PR에 한 스택만 활성화하여 DNS, Platform, App을 순차 검증한다.
8. 모든 스택의 정상 배포·드리프트·복구 절차를 확인한 뒤에만 기존 배포 역할을 폐기한다. 기존 역할은 전환 중 삭제하지 않는다.

## 향후 권한 추가 규칙

새 리소스나 새 템플릿을 활성화할 때는 다음 변경을 하나의 보안 검토 단위로 다룬다.

1. 템플릿에 실제 선언된 AWS 리소스와 참조 리소스를 구분한다.
2. CloudFormation handler가 필요한 API, ARN, `iam:PassRole` 대상을 확인한다.
3. 해당 그룹 실행 역할에 최소 action·ARN만 추가한다.
4. GitHub 배포 역할에는 해당 스택 ARN과 필요한 실행 역할 전달 권한만 추가한다.
5. `stacks.json`의 `role_group`, `enabled`, 의존성을 검토한다.
6. Policy Simulator/Access Analyzer, CI, Change Set을 통과한 뒤 활성화한다.

ECR 또는 모니터링 기술 스택이 확정될 때까지 관련 권한은 추가하지 않는다.

### 공식 권한 모델 확인

- [ECS 서비스 권한 표](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ecs.html): DescribeClusters/DescribeServices는 ARN 제한을 지원하지만 DescribeTaskDefinition은 지원하지 않는다.
- [Application Auto Scaling 권한 표](https://docs.aws.amazon.com/service-authorization/latest/reference/list_application-autoscaling.html): DescribeScalableTargets는 Resource `*`, Register/DeregisterScalableTarget은 기존 대상 ARN으로 제한한다.
- [S3 암호화 API 권한](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutBucketEncryption.html): API 이름과 IAM Action 이름이 다르므로 Get/PutEncryptionConfiguration을 사용한다.

정적 테스트는 실제 CloudFormation 공급자 호출 전체를 증명하지 않는다. AWS 사전 정책 검증과 스택별 검증 전에는 운영 적용 완료로 간주하지 않는다. 신규 생성 및 롤백 복구에 필요한 API도 Change Set별로 별도 검토한다.

## 신규 RDS 생성 권한

`audigo-db`는 Platform 실행 역할만 사용한다. GitHub 역할에는 DB 스택 조회, plan subject의 Platform 역할 지정 Change Set 생성, production subject의 실행 범위를 추가한다.

- RDS 쓰기는 `db:audigo-prd-db`, `subgrp:audigo-prd-db-subnets`, `pg:audigo-prd-db-parameters`로 제한한다. 기존 네트워크에는 Describe만 허용한다.
- DB 생성·수정 시 그룹 연결을 인가하려고 위 두 그룹과 기본 option group `og:default:mysql-8-4`에도 CreateDBInstance/ModifyDBInstance를 허용한다. option group 내용 변경 API는 부여하지 않는다. [공식 연결 권한 예시](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/security_iam_id-based-policy-examples-create-and-modify-examples.html)를 따른다.
- 관리형 Secret은 RDS가 이름을 정하므로 같은 계정/리전의 `secret:rds!db-*`에 CreateSecret/TagResource만 허용한다. 다른 RDS 관리형 Secret도 이 이름 패턴에 포함되는 한계가 있으며 GetSecretValue는 없다.
- KMS DescribeKey는 `alias/aws/rds`, `alias/aws/secretsmanager`로 제한한다. CreateGrant는 `alias/aws/rds`, RDS 경유, AWS 리소스 grant로 제한한다. CMK를 도입하려면 템플릿·정확한 키 ARN·키 정책을 함께 변경한다.
- `AWSServiceRoleForRDS`가 계정에 있어야 한다. 없으면 관리자가 먼저 준비한다. 실행 역할에 IAM 역할 생성·수정 권한은 추가하지 않는다.
- AWS 관리형 키와 서비스 연결 역할의 실제 상태, SCP/권한 경계, 정책 시뮬레이션과 CREATE Change Set은 배포 전에 별도로 검증한다.

[RDS 생성 가이드](rds-create.md)와 [RDS 공급자 소스](https://github.com/aws-cloudformation/aws-cloudformation-resource-providers-rds)의 handler 권한을 참고한다. 공급자 권한 목록은 복원·Replica·Aurora까지 포함하므로 이번 템플릿이 사용하는 기능의 API만 반영했다.
