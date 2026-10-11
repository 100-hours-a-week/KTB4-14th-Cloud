# Audigo Infrastructure

AWS CloudFormation으로 인프라를 관리하는 저장소입니다.

## 파일 구조

템플릿 파일은 `cloudformation/`에서 관리합니다.

| 파일 | 관리할 내용 |
|---|---|
| `network.yaml` | VPC, 서브넷, IGW, 라우팅, 보안 그룹 |
| `db.yaml` | RDS, DB Subnet Group |
| `cache.yaml` | Valkey |
| `sqs.yaml` | FIFO 큐 4개와 DLQ |
| `alb.yaml` | ALB, 리스너, 대상 그룹, 경로 규칙 |
| `frontend.yaml` | 프론트엔드 S3, CloudFront |
| `ecs-common.yaml` | ECS 클러스터, 공통 실행 역할 |
| `rest-api.yaml` | REST Task Definition, Service, Task Role, Auto Scaling |
| `websocket.yaml` | WS Task Definition, Service, Task Role, Auto Scaling |
| `ai-api.yaml` | AI API Task Definition, Service, Task Role, 확장 설정 |
| `monitoring.yaml` | 공통 모니터링과 알림 |

## 현재 상태

파일 구조만 생성한 초기 단계입니다. 템플릿은 비어 있으며 아직 배포할 수 없습니다.

## 보안

AWS 인증 정보, 환경 변수 및 개인 키는 커밋하지 않습니다.
로컬 IDE 설정과 에이전트 실행 상태도 `.gitignore`로 제외합니다.
