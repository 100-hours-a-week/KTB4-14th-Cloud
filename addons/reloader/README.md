# Secret 변경 시 자동 재시작

## 구성

ESO가 Kubernetes Secret의 값을 갱신하면 Reloader가 대상 Deployment의
Pod 템플릿 annotation을 변경하여 롤링 재시작을 유도한다.

- Helm 차트: 2.2.19
- 설치 namespace: reloader
- 감시 범위: 백엔드 운영·스테이징 8개 namespace와 reloader namespace
- ConfigMap 변경 및 Job·CronJob 재실행 제외
- 대상 annotation을 설정한 워크로드에 적용
- AWS 원본 변경은 ESO 동기화 주기인 5분을 고려한다.

## 1. Deployment 설정

Deployment의 metadata.annotations에 추가한다.
spec.template.metadata.annotations에 넣는 설정이 아니다.

    metadata:
      annotations:
        secret.reloader.stakater.com/reload: "backend-secrets"

컨테이너에서는 같은 namespace의 Secret을 참조한다.

    envFrom:
      - secretRef:
          name: backend-secrets

Reloader는 Deployment의 배포 전략과 readiness 설정에 따라 롤링 교체를 유도한다.
서비스에 맞는 readiness probe와 RollingUpdate 설정을 함께 구성한다.

## 2. Argo CD Application 설정

해당 Deployment를 관리하는 Application의 spec에 추가한다.
기존 ignoreDifferences와 syncOptions가 있다면 항목을 합쳐서 적용한다.

    ignoreDifferences:
      - group: apps
        kind: Deployment
        jsonPointers:
          - /spec/template/metadata/annotations/reloader.stakater.com~1last-reloaded-from

    syncPolicy:
      syncOptions:
        - ServerSideApply=true
        - RespectIgnoreDifferences=true

이 설정은 해당 Application이 관리하는 Deployment에서
Reloader가 변경하는 annotation 하나만 차이 비교에서 제외한다.
전체 Pod 템플릿이나 전체 annotations를 제외하지 않는다.

## 검증 결과

auth-staging의 별도 테스트 Secret과 Deployment로 확인했다.

- 테스트 값 v1의 초기 환경변수 주입 성공
- Secret을 v2로 변경한 뒤 Reloader가 변경 감지
- 기존 Pod 삭제 및 새 Pod 정상 기동
- 새 컨테이너에서 v2 반영 확인: SECRET_AUTO_RESTART_OK
- 재시작용 annotation 유지
- Argo CD: Synced / Healthy
- 검증 후 임시 Application·Deployment·Secret 삭제 완료

검증 자료는 tests/manual/reloader에 보관한다.
실제 서비스 전체의 연결 검증은 포함하지 않았다.

## 운영 시 주의사항

- 대상 설정이 없는 Deployment는 자동 재시작되지 않는다.
- 변경된 값이 올바른 자격 증명인지는 Reloader가 판단하지 않는다.
- 잘못된 값도 재시작을 유발하므로 변경 후 서비스 상태를 확인한다.
- 자동 재시작은 이전 시크릿 값으로의 자동 롤백을 제공하지 않는다.
- 변경 실패 시 복구 절차는 별도 후속 작업이다.
