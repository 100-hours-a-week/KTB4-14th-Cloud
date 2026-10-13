# Audigo Infrastructure

AWS CloudFormation으로 Audigo 인프라를 관리하는 저장소입니다.

> **현재 단계:** 기존 리소스의 코드 목록을 정리하고 AWS 조회 결과와 비교하는 단계입니다.
> 최신 IaC 생성기 템플릿에는 **74개 리소스·30개 유형**, 추가된 원본 스택 템플릿에는 **ECS 서비스 선언 7개**가 있습니다.
> Mock AI 중복을 제외한 기존 리소스 코드의 비교 기준선은 **80개 항목**이며, `network.yaml`의 신규 초안 9개는 별도입니다.
> 나머지 기능별 템플릿 10개는 비어 있습니다. 저장소의 템플릿에 대한 실제 편입·배포 검증은 아직 수행하지 않았습니다.

## 목표와 리소스 목록

기존 인프라를 코드로 정리하고 CloudFormation으로 관리한 뒤, **ECS on EC2 → Fargate**, **EC2 MySQL → RDS**로 전환합니다.
V2가 안정화되면 V1의 불필요한 리소스를 정리합니다.

**[기존 리소스 목록 및 AWS 비교 문서](docs/resource-inventory.md)**에서 다음을 확인할 수 있습니다.

- 스캔 템플릿의 전체 논리 ID·리소스 유형·실제 ID 또는 비교 키
- 제공된 AWS 스캔, ECS 서비스·태스크 정의, EC2 조회 결과와의 비교
- 추가된 원본 스택 템플릿 7개와 기존 스택이 관리하는 서비스
- 원본 템플릿과 스캔·제공된 실제 ECS 조회의 설정 차이 (Mock AI `:3` / `:6`)
- 신규 네트워크 초안 9개와 기존 리소스의 구분, 편입 전 확인사항

> 스캔 템플릿의 리소스는 모두 `Retain` 정책입니다. 코드에서 제거해도 AWS 리소스가 삭제되는 것은 아니므로,
> V1 정리 시 삭제 정책과 의존성을 별도로 검토해야 합니다. **원본 서비스 스택 7개에는 이 보존 정책이 선언되어 있지 않습니다.**
> 자세한 근거는 리소스 목록 문서에 정리했습니다.

## 기능별 템플릿 구성 (목표 구조)

기능별 신규 구성 초안은 [`cloudformation/`](cloudformation/)에 있습니다.
기존 리소스 자료는 [`audigo-existing-stacks-20261012-100305/`](audigo-existing-stacks-20261012-100305/)에 있으며, 스캔 YAML 2개와 원본 서비스 스택의 JSON `.template` 7개를 보관합니다.

| 파일 | 관리 범위 |
|---|---|
| [`network.yaml`](cloudformation/network.yaml) | 기존 VPC·퍼블릭 서브넷 참조, 프라이빗 서브넷, 라우팅, 보안 그룹 |
| [`db.yaml`](cloudformation/db.yaml) | RDS, DB Subnet Group |
| [`cache.yaml`](cloudformation/cache.yaml) | Valkey |
| [`sqs.yaml`](cloudformation/sqs.yaml) | FIFO 큐 4개와 DLQ |
| [`alb.yaml`](cloudformation/alb.yaml) | ALB, 리스너, 대상 그룹, 경로 규칙 |
| [`frontend.yaml`](cloudformation/frontend.yaml) | 프론트엔드 S3, CloudFront |
| [`ecs-common.yaml`](cloudformation/ecs-common.yaml) | ECS 클러스터, 공통 실행 역할 |
| [`rest-api.yaml`](cloudformation/rest-api.yaml) | REST Task Definition, Service, Task Role, Auto Scaling |
| [`websocket.yaml`](cloudformation/websocket.yaml) | WS Task Definition, Service, Task Role, Auto Scaling |
| [`ai-api.yaml`](cloudformation/ai-api.yaml) | AI API Task Definition, Service, Task Role, 확장 설정 |
| [`monitoring.yaml`](cloudformation/monitoring.yaml) | 공통 모니터링과 알림 |

## 신규 네트워크 초안 (`network.yaml`)

### 기존 인프라

아래는 현재 인프라 정보입니다. `network.yaml`은 VPC와 퍼블릭 서브넷을 **ID로 참조**하며,
기존 VPC·서브넷·IGW·라우팅·엔드포인트를 생성하거나 변경하지 않습니다.
기존 리소스를 이 스택의 관리 대상으로 가져오는 것도 아닙니다.

| 리소스 | 이름 / ID | 설정 |
|---|---|---|
| VPC | `audigo-vpc` / `vpc-04129874f9fd135c6` | `10.0.0.0/16`, IPv6 없음 |
| 퍼블릭 서브넷 A | `audigo-prd-pub-2a` | `ap-northeast-2a`, `10.0.0.0/20` |
| 퍼블릭 서브넷 B | `audigo-prd-pub-2b` | `ap-northeast-2b`, `10.0.16.0/20` |
| 퍼블릭 라우팅 테이블 | `audigo-prd-pub-rt` | 퍼블릭 서브넷 2개 연결, IGW 경로 |
| 메인 라우팅 테이블 | `audigo-prd-main-rt` | 명시적인 서브넷 연결 없음, 로컬 경로 |
| 인터넷 게이트웨이 | `audigo-prd-igw` | 퍼블릭 서브넷 인터넷 연결 |
| S3 게이트웨이 엔드포인트 | `audigo-prd-vpce` | S3 접근용 |

### 새로 생성할 리소스

| 리소스 | 기본 설정 |
|---|---|
| 프라이빗 서브넷 A | `ap-northeast-2a`, `10.0.32.0/20` |
| 프라이빗 서브넷 B | `ap-northeast-2b`, `10.0.48.0/20` |
| 프라이빗 라우팅 테이블 | 프라이빗 서브넷 2개 연결, 기본은 VPC 내부 경로만 사용 |
| 보안 그룹 | ALB, ECS, DB, Valkey용 각 1개 |

리소스 `Name` 태그의 기본 접두사는 `audigo-prd`입니다.
**NAT Gateway와 VPC Endpoint는 생성하지 않습니다.**

### 보안 그룹 인바운드

| 대상 | 허용 출발지 | 기본 TCP 포트 |
|---|---|---|
| ALB | 모든 IPv4 주소 (`0.0.0.0/0`) | `80`, `443` |
| ECS | ALB 보안 그룹 | REST `8080`, WS `8081`, AI `8000` |
| DB | ECS 보안 그룹 | `3306` (MySQL 가정) |
| Valkey | ECS 보안 그룹 | `6379` |

- 서비스 포트는 파라미터로 변경할 수 있습니다. 실제 애플리케이션·컨테이너·대상 그룹 포트와 맞춰야 합니다.
- REST·WS·AI는 현재 하나의 ECS 보안 그룹을 공유합니다. 내부 AI 호출이 필요하면 별도 인바운드 규칙이 필요합니다.
- 보안 그룹의 기본 아웃바운드는 전체 허용입니다.
- HTTPS 리스너와 인증서는 추후 `alb.yaml`에서 구성합니다.

## 배포 전 확인사항

### 파라미터

| 파라미터 | 기본값 / 입력할 값 |
|---|---|
| `NamePrefix` | `audigo-prd` |
| `ExistingVpcId` | `vpc-04129874f9fd135c6` |
| `ExistingPublicSubnetAId` | **필수 입력:** `audigo-prd-pub-2a`의 실제 `subnet-...` ID |
| `ExistingPublicSubnetBId` | **필수 입력:** `audigo-prd-pub-2b`의 실제 `subnet-...` ID |
| `PrivateSubnetAAvailabilityZone` | `ap-northeast-2a` |
| `PrivateSubnetBAvailabilityZone` | `ap-northeast-2b` |
| `PrivateSubnetACidr` | `10.0.32.0/20` |
| `PrivateSubnetBCidr` | `10.0.48.0/20` |

### 체크리스트

- [ ] 배포 리전이 `ap-northeast-2`인지 확인
- [ ] 퍼블릭 서브넷 ID 2개 입력 및 지정한 VPC 소속 여부 확인
- [ ] 두 퍼블릭 서브넷이 서로 다른 AZ에 있고 IGW 경로가 설정되어 있는지 확인
- [ ] 신규 프라이빗 CIDR이 VPC 범위 안에 있고 기존 모든 서브넷과 겹치지 않는지 확인
- [ ] 서비스별 실제 포트 확인
- [ ] ECS 이미지 다운로드·로그 전송·외부 API 호출에 필요한 네트워크 연결 구성 확인

### 기존 S3 엔드포인트 연결

새 프라이빗 서브넷에서 기존 S3 게이트웨이 엔드포인트를 사용하려면:

1. 배포 결과에서 `PrivateRouteTableId`를 확인합니다.
2. 기존 엔드포인트 `audigo-prd-vpce`의 라우팅 테이블 연결에 해당 ID를 추가합니다.
3. 기존 라우팅 테이블 연결은 유지합니다.

> 이 연결은 템플릿에서 자동으로 수행하지 않습니다.
> S3 엔드포인트만으로는 ECR API, CloudWatch Logs 및 외부 API 접근을 해결할 수 없습니다.
> ECS 배포 전에 필요한 VPC Endpoint 또는 NAT 구성을 추가해야 합니다.

## 보안 및 로컬 파일

AWS 인증 정보, 환경 변수 및 개인 키는 커밋하지 않습니다.
`.gitignore`에서 다음 로컬 파일을 제외합니다.

- IDE 설정: `.idea/`
- 에이전트 실행 상태: `.omx/`
- AWS 인증 정보: `.aws/`
- 환경 변수: `.env`, `.env.*` (`.env.example` 제외)
- 개인 키: `*.pem`, `*.key`
- macOS 파일: `.DS_Store`

## 인프라 CI/CD

전체 CloudFormation 템플릿 검증과 main 병합 시 자동 계획 생성·승인 후 배포를 지원합니다.
`Run workflow`로 전체 또는 개별 스택 계획을 재실행할 수도 있습니다.
현재 배포 활성화 대상은 `audigo-network`이며, 운영 적용 전 IAM 및 GitHub Environment 설정이 필요합니다.

- [CI/CD 설정 및 운영 가이드](docs/infrastructure-cicd.md)
- [파일과 스택 매핑](deploy/stacks.json)
