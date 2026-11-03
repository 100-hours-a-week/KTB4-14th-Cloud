# ESO 수동 검증 자료

이 디렉터리는 현재 Argo CD Application의 자동 배포 대상이 아니다.

## 백엔드 주입 검증

- backend/production.yaml: 운영 네 namespace의 검증 Job
- backend/staging.yaml: 스테이징 네 namespace의 검증 Job
- Job 이름: backend-secret-injection-check

스테이징 실행 예시:

    kubectl apply -f tests/manual/external-secrets/backend/staging.yaml

각 namespace에서 Job 완료와 SECRET_INJECTION_OK 로그를 확인한다.

    kubectl -n auth-staging wait --for=condition=complete job/backend-secret-injection-check --timeout=180s
    kubectl -n auth-staging logs job/backend-secret-injection-check

검증 후 실행한 파일에 해당하는 Job을 정리한다.

    kubectl delete -f tests/manual/external-secrets/backend/staging.yaml --ignore-not-found

동일한 완료 Job은 apply만으로 재실행되지 않는다.
재검증 시 해당 테스트 Job을 삭제한 뒤 다시 생성한다.

## 초기 연결 검증 자료

auth-staging/은 app namespace에서 사용했던 초기 테스트 자료다.
별도 AWS 테스트 시크릿과 IAM 역할에 의존한다.
현재 백엔드 시크릿 사용에는 필요하지 않으며 일괄 적용하지 않는다.

## 기존 리소스 정리

현재 Application은 prune:false이므로 Git 파일 이동·삭제만으로
기존 클러스터 리소스가 삭제되지 않는다.

정리 PR 병합 후 초기 테스트 Application과 테스트 리소스를 별도로 삭제한다.
운영·스테이징의 backend-secrets와 연결 설정은 유지한다.
