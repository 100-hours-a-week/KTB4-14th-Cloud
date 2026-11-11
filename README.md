# Audigo Infrastructure & GitOps

Audigo 서비스의 **AWS 인프라와 Kubernetes 배포 설정을 코드로 관리하는 Cloud 저장소**입니다.
AWS 리소스는 **CloudFormation 기반 IaC(Infrastructure as Code)** 로 정의하고,
Kubernetes 애플리케이션과 운영 도구는 **Helm과 Argo CD 기반 GitOps**로 관리합니다.

인프라 템플릿, 서비스·환경별 배포 설정, 모니터링, 시크릿 연동 및 GitHub Actions 워크플로를 함께 보관하여
변경 내용을 Git에서 검토하고 배포 이력을 추적할 수 있도록 구성합니다.
Spring Boot 백엔드와 AI 서비스의 이미지는 각 소스 저장소에서 빌드하며, 이 저장소에서는 검증된 이미지의 배포 설정과 운영 승격을 관리합니다.

## 관리 범위 (요약하기)

**IaC 영역**에서는 CloudFormation으로 네트워크, DNS, RDS, Valkey, SQS, ALB, S3·CloudFront 및 ECS 관련 AWS 리소스를 관리합니다.
인프라 템플릿과 배포 설정은 `cloudformation/`, `deploy/`, `bootstrap/`에 보관하며,
GitHub Actions에서 템플릿을 검증하고 Change Set을 생성한 뒤 검토·승인을 거쳐 변경 사항을 적용합니다.

**GitOps 영역**에서는 Helm과 Argo CD로 백엔드·AI 워크로드, 시크릿 연동, 모니터링 및 클러스터 운영 도구의 Kubernetes 배포 설정을 관리합니다.
공통 Chart와 서비스·환경별 values는 `apps/`에, Application 선언은 `argocd-apps/`에,
운영 도구와 모니터링 설정은 `addons/`, `monitoring/`에 보관합니다.
GitHub Actions를 통해 검증된 이미지 정보를 values에 반영하고, Argo CD가 각 Application의 자동·수동 동기화 정책에 따라 클러스터에 적용합니다.

## 폴더 구조

```text
.
├── .github/workflows/              # 인프라 검증·적용, 이미지 갱신·운영 승격 워크플로
├── cloudformation/                 # AWS 리소스별 CloudFormation 템플릿
├── deploy/                         # IaC 실행 설정과 운영 배포 기준선
│   ├── stacks.json                 # 스택·템플릿·실행 역할·활성화 여부 매핑
│   ├── parameters/                 # 스택별 파라미터
│   ├── imports/                    # 기존 AWS 리소스 편입용 식별자 자료
│   ├── iam/                        # 이전 network 전용 IAM 참고 정책
│   ├── backend-production-baseline.json
│   └── ai-production-baseline.json # 서비스별 운영 배포 기준선
├── bootstrap/                      # 초기 설치 및 별도 관리자용 IAM 구성
│   ├── argocd/                     # Argo CD Helm values
│   ├── cilium/                     # Cilium values 및 설치 스크립트
│   ├── aws-lb-controller/           # AWS Load Balancer Controller 설정·권한·설치
│   └── install.sh                  # Argo CD 초기 설치 스크립트
├── argocd-apps/                     # Argo CD Application 선언
│   ├── root.yaml                   # 하위 Application을 관리하는 Root Application
│   └── applications/               # 서비스·운영 도구별 Application
│       ├── spring/                 # 백엔드 운영·스테이징
│       ├── ai/                     # AI 운영·스테이징 및 시크릿 연동
│       └── monitoring/             # 모니터링 스택 및 모니터링용 ESO
├── apps/                           # 애플리케이션 Helm 배포 설정
│   ├── chart/                      # Spring Boot·AI 공통 Helm Chart
│   ├── spring-value/               # 백엔드 서비스·환경별 values
│   └── ai-values/                  # AI API·Worker 및 스테이징 values
├── addons/                         # 클러스터 운영 도구 설정
│   ├── external-secrets/           # ESO values와 백엔드·AI SecretStore/ExternalSecret
│   ├── external-secrets-monitoring/ # 모니터링용 ESO 및 시크릿 리소스
│   ├── reloader/                   # Secret 변경 시 대상 워크로드 재시작 설정
│   └── metrics-server/             # Kubernetes 리소스 메트릭 수집 설정
├── monitoring/                     # 관측성 도구별 Helm values
│   ├── kube-prometheus-stack/      # Prometheus·Grafana·Alertmanager
│   ├── loki/                       # 로그 저장·조회
│   ├── alloy/                      # Pod 로그 수집·전송
│   └── yace/                       # CloudWatch SQS 메트릭 수집
├── releases/                       # 기존 AI Worker ECS 배포용 릴리스 명세
├── scripts/                        # IaC 계획·적용, 후보 검증·승격·기준선 갱신 로직
├── tests/                          # 배포 안전장치·워크플로 테스트 및 수동 검증 자료
└── docs/                           # 리소스별 운영·편입 가이드와 GitOps 배포 문서
    └── gitops/                     # 백엔드·AI 운영 승격 및 배포 기준선 절차
```

## IaC: CloudFormation 기반 인프라 관리

AWS 리소스를 기능별 템플릿으로 나누고, [`deploy/stacks.json`](deploy/stacks.json)에서
템플릿·파라미터·실행 역할 그룹·배포 활성화 여부를 연결합니다.
기존 리소스의 CloudFormation 편입(IMPORT)과 신규 리소스 생성은 개별 가이드에 따라 구분합니다.

### 인프라 변경 흐름

```text
CloudFormation·배포 설정 변경
  → PR에서 템플릿·설정 검증 및 안전장치 테스트
  → main 반영 시 활성 스택 전체 Change Set 계획 생성
  → 계획 검토 및 iac-production Environment 승인
  → 검토한 동일 Change Set 적용
```

- **CI:** [`cloudformation-ci.yml`](.github/workflows/cloudformation-ci.yml)에서 템플릿 lint, 배포 설정 검증, Python 테스트를 수행합니다.
- **CD:** [`cloudformation-cd.yml`](.github/workflows/cloudformation-cd.yml)에서 실제 AWS 상태와 비교한 계획을 생성하고, 변경이 있는 경우 승인 후 적용합니다. 전체·개별 스택의 수동 재실행도 지원합니다.
- **권한 분리:** GitHub OIDC 인증과 Core / Platform / App CloudFormation 실행 역할을 사용합니다. IAM 기준은 [`bootstrap/infra-iam.yaml`](bootstrap/infra-iam.yaml)이며 별도 관리자가 적용합니다.
- **변경 제한:** 삭제·교체 변경을 차단하고, IMPORT는 일반 CD에서 수행하지 않습니다. 신규 생성은 스택별 `allow_create` 설정과 권한 준비가 필요합니다.

실제 승인 적용에는 GitHub Environment 보호 규칙과 배포 활성화 변수 설정이 필요합니다.
설정 방법과 실패 처리 절차는 [CloudFormation CI/CD 운영 가이드](docs/infrastructure-cicd.md),
[그룹별 IAM 운영 가이드](docs/iam-group-permissions.md)를 참고합니다.

## GitOps: Helm과 Argo CD 기반 배포 관리

서비스의 배포 상태를 Helm Chart와 values로 선언하고, Argo CD가 Git의 설정과 클러스터 상태를 비교·동기화하도록 구성합니다.
Application의 저장소 참조는 `main`을 기준으로 하며, 외부 Helm Chart는 Application에 지정한 버전을 사용합니다.

### Application과 Helm values의 관계

```text
argocd-apps/root.yaml
  → argocd-apps/applications/ 아래의 Application 관리
    → 애플리케이션: apps/chart + 서비스·환경별 values
    → 운영 도구: 외부 Helm Chart + addons/ 또는 monitoring/의 values
  → 각 Application의 대상 namespace에 동기화
```

공통 Chart인 [`apps/chart/`](apps/chart/)는 Deployment, Service, Ingress, ConfigMap,
ExternalSecret, HPA, NetworkPolicy, ServiceMonitor/PodMonitor 템플릿을 제공합니다.
리소스 생성 여부와 이미지·리소스 요청량·probe·환경변수 등은 서비스별 values로 제어합니다.

| 구분 | values 경로 | 주요 대상 |
| --- | --- | --- |
| 백엔드 | [`apps/spring-value/`](apps/spring-value/) | auth, chat API·WS, travel API·Worker, general API·WS |
| AI | [`apps/ai-values/`](apps/ai-values/) | match-ai, travel-ai, travel-ai-worker, ai-staging |
| 운영 도구 | [`addons/`](addons/), [`monitoring/`](monitoring/) | ESO, Reloader, Metrics Server, 모니터링 스택 |

### 환경별 동기화 정책

| 대상 | 동기화 | 설정상 특징 |
| --- | --- | --- |
| 백엔드·AI 운영 앱 | 수동 Sync | 운영 values 반영 후 담당자가 Application별 배포 수행 |
| 백엔드 스테이징 앱 | 자동 Sync | `selfHeal: true`, `prune: false` |
| AI 스테이징 앱 | 자동 Sync | `selfHeal: true`, `prune: true` |
| Root Application, Argo CD, 백엔드·AI ESO, 시크릿 Application, Reloader | 자동 Sync | `selfHeal: true`, `prune: false` |
| 모니터링 Application, 모니터링용 ESO, Metrics Server | 자동 Sync | `selfHeal: true`, `prune: true` |

`selfHeal`은 클러스터에서 발생한 설정 차이를 Git의 선언에 맞추고,
`prune`은 Git에서 제거된 리소스를 동기화 시 삭제할지 결정하는 설정입니다.
Root Application의 자동 동기화와 개별 운영 앱의 수동 동기화는 각각의 Application에 설정되어 있습니다.

### 이미지 갱신과 운영 승격

스테이징 워크플로는 소스 저장소의 이미지 발행 run·artifact와 이미지 digest·ARM64 지원 여부를 검증한 뒤,
스테이징 values와 후보 기록을 Git에 반영합니다. 이미지는 재빌드하지 않습니다.

```text
소스 저장소에서 이미지 발행
  → 스테이징 후보 검증 및 values 갱신
  → Argo CD 자동 Sync 후 스테이징 기능 확인
  → 운영 승격 미리보기·검토 및 PR 생성
  → 운영 values PR 병합
  → 운영 Application 수동 Sync 및 실제 이미지·기능 확인
  → 운영 배포 기준선 갱신 PR 검토·병합
```

운영 values와 실제 배포 확인 기록은 따로 관리합니다.
[`deploy/backend-production-baseline.json`](deploy/backend-production-baseline.json)과
[`deploy/ai-production-baseline.json`](deploy/ai-production-baseline.json)은 운영 Sync 후 확인한 버전의 기준선입니다.
후보 기록이나 PR 병합만으로 실제 Pod 배포·기능 검증이 완료된 것으로 처리하지 않습니다.

- [백엔드 운영 승격](docs/gitops/backend-production.md)
- [AI 운영 승격](docs/gitops/ai-production.md)
- [운영 배포 기준선 갱신](docs/gitops/production-baseline.md)

기존 AI Worker ECS 배포는 [`releases/ai.yml`](releases/ai.yml)과
[`ai-deploy.yml`](.github/workflows/ai-deploy.yml)을 사용하는 별도 경로입니다.

## 시크릿과 모니터링

### 시크릿 관리

실제 비밀값은 AWS Secrets Manager에 두고, External Secrets Operator(ESO)가
SecretStore·ExternalSecret 설정에 따라 Kubernetes Secret으로 동기화합니다.
Reloader는 감시 범위와 대상 annotation이 설정된 워크로드에 대해 Secret 변경 시 Pod 롤링 재시작을 유도합니다.

```text
AWS Secrets Manager → ESO → Kubernetes Secret → Reloader → 대상 Pod 롤링 재시작
```

운영 앱의 수동 Sync 정책과 Secret 동기화·재시작은 별도 흐름입니다.
시크릿 변경은 운영 앱의 수동 Sync 없이도 대상 Pod 재시작을 유발할 수 있습니다.
설정과 검증 자료는 [백엔드 시크릿 사용법](addons/external-secrets/README.md),
[Reloader 사용법](addons/reloader/README.md), [`tests/manual/`](tests/manual/)에서 확인할 수 있습니다.

### 모니터링 구성

[`argocd-apps/applications/monitoring/`](argocd-apps/applications/monitoring/)에서 외부 Helm Chart와
이 저장소의 values를 연결해 모니터링 스택을 관리합니다.

| 도구 | 역할 | 설정 |
| --- | --- | --- |
| kube-prometheus-stack | Prometheus 메트릭 수집, Grafana 시각화, Alertmanager 알림 | [`values.yaml`](monitoring/kube-prometheus-stack/values.yaml) |
| Alloy → Loki | 각 노드의 Pod 로그를 수집해 Loki로 전송·저장 | [Alloy](monitoring/alloy/values.yaml), [Loki](monitoring/loki/values.yaml) |
| YACE | CloudWatch의 SQS 메트릭을 Prometheus에 제공 | [`values.yaml`](monitoring/yace/values.yaml) |
| Metrics Server | Kubernetes 리소스 메트릭 제공 | [`values.yaml`](addons/metrics-server/values.yaml) |

## 보안 및 로컬 파일

AWS 인증 정보, 실제 비밀값, 개인 키는 Git에 커밋하지 않습니다.
일반 설정은 ConfigMap·values·배포 파라미터로, 비밀값은 Secrets Manager 연동으로 관리합니다.

`.gitignore`에서는 `.aws/`, `.env`, `.env.*`(`.env.example` 제외), `*.pem`, `*.key`,
IDE 설정, `.omx/`, `.venv/`, Python 캐시와 macOS 파일 등을 제외합니다.

서비스·환경·릴리스 명명 규칙은 [명명 규칙 문서](docs/naming-conventions.md)를 참고합니다.
