# 백엔드 시크릿 사용법

## 구성

AWS Secrets Manager의 비밀값을 ESO가 Kubernetes Secret으로 동기화한다.
실제 비밀값은 Git에 저장하지 않는다.

| 환경 | Secrets Manager 객체 |
| --- | --- |
| 운영 | audigo/k8s/backend/production |
| 스테이징 | audigo/k8s/backend/staging |

| 기능 | 운영 namespace | 스테이징 namespace |
| --- | --- | --- |
| 인증 | auth-prod | auth-staging |
| 채팅 | chat-prod | chat-staging |
| 여행 | travel-prod | travel-staging |
| 일반 | general-prod | general-staging |

각 namespace의 리소스 이름:

- SecretStore: aws-backend
- ExternalSecret: backend-secrets
- Kubernetes Secret: backend-secrets

같은 환경의 네 namespace는 같은 비밀값을 공유한다.

## 환경변수 키

| 용도 | 운영 | 스테이징 |
| --- | --- | --- |
| AI API 인증 | AUDIGO_API_TOKEN | AUDIGO_API_TOKEN |
| JWT 서명 | AUDIGO_JWT_SECRET | AUDIGO_JWT_SECRET |
| DB 비밀번호 | DB_PASSWORD | DB_PASSWORD |
| Kakao API 인증 | KAKAO_REST_API_KEY | KAKAO_REST_API_KEY |
| Redis 비밀번호 | SPRING_DATA_REDIS_PASSWORD | AUDIGO_REDIS_PASSWORD |
| TAGO API 인증 | TAGO_SERVICE_KEY | TAGO_SERVICE_KEY |

## Deployment에서 사용

컨테이너 설정에 추가한다.

    envFrom:
      - secretRef:
          name: backend-secrets

Pod와 Secret은 같은 namespace에 있어야 한다.
비밀값을 Deployment YAML에 직접 적지 않는다.
일반 환경변수는 별도 ConfigMap으로 관리한다.

## 상태 확인

auth-staging 기준 예시:

    kubectl -n auth-staging get secretstore aws-backend
    kubectl -n auth-staging get externalsecret backend-secrets
    kubectl -n auth-staging get secret backend-secrets

정상 상태:

- SecretStore: Valid / READY=True
- ExternalSecret: SecretSynced / READY=True
- Secret: Opaque / DATA=6

## 변경 시 주의사항

- 동기화 주기는 5분이다.
- 환경변수로 주입된 값은 기존 컨테이너에서 자동 갱신되지 않는다.
- 자동 재시작은 대상 annotation과 Application 설정이 적용된 Deployment에서 동작한다.
- 적용 방법은 [Reloader 사용법](../reloader/README.md)을 참고한다.
- 자동 재시작을 적용하지 않은 Deployment는 수동으로 재시작한다.
- 기존 ECS용 audigo/v1/backend와 신규 객체는 자동으로 동기화되지 않는다.
- 운영·스테이징에서 공유하는 자격 증명이 있으므로 변경 전 사용처를 확인한다.
- DB·Redis 비밀번호는 대상 시스템의 실제 값과 일치해야 한다.
- 비밀값을 로그, 채팅, PR에 남기지 않는다.

## 검증 범위

8개 namespace에서 여섯 개 필수 환경변수의 주입을 검증했다.
각 검증 Job에서 SECRET_INJECTION_OK를 확인했다.
실제 DB·Redis·외부 API 연결 검증은 포함하지 않았다.

검증 YAML은 tests/manual/external-secrets에 보관한다.
자동 재시작은 auth-staging의 별도 테스트 Deployment로 검증했다.
변경 실패 시 복구 절차는 후속 작업이다.
