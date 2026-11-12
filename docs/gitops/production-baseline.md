# 운영 배포 기준선 갱신

운영 승격 PR을 Cloud `main`에 병합하고 Argo CD에서 수동 Sync한다. 담당자는
대상 앱의 롤아웃 완료, Pod Ready, 실제 실행 이미지 및 주요 기능을 확인한 뒤
**운영 배포 기준선 갱신**을 실행한다. 수동 실행 자체를 담당자의 확인으로
기록하며, Actions는 병합된 승격 PR과 Git의 운영 이미지를 검증한다.

## 실행 방법

Cloud 저장소 **Actions → 운영 배포 기준선 갱신 → Run workflow**에서
브랜치를 `main`으로 두고 다음 값을 입력한다.

| 입력 | 값 |
| --- | --- |
| `service` | 백엔드는 `backend`, AI는 `ai` |
| `promotion_pr` | 운영 검증을 마친 Cloud 운영 승격 PR 번호 |

CLI에서는 다음처럼 실행한다. `83`과 `89`는 기존 운영 승격 PR 번호다.

```sh
gh workflow run production-baseline.yml --ref main -f service=backend -f promotion_pr=83
gh workflow run production-baseline.yml --ref main -f service=ai -f promotion_pr=89
```

워크플로는 승격 PR의 병합 여부, 성공한 승격 실행, 발행 후보 기록과 현재
`main`의 운영 values를 대조한다. 모두 일치하면 승격 대상 앱만 기준선 JSON에
반영하는 PR을 생성하고 실행 Summary에 링크를 표시한다. 기준선에는 수동
실행자와 UTC 실행 시각, 승격 PR 및 후보 정보를 기록한다.

## 재실행과 병합

같은 승격 PR을 다시 입력하면 열린 기준선 PR을 재사용한다. 이미 반영됐다면
성공으로 종료한다. 현재 운영 values가 다른 이미지로 변경되거나 롤백됐다면
갱신을 중단한다.

Cloud 팀은 생성된 PR을 검토하고 수동 병합한다. 기준선 PR에는
`Production baseline freshness` 검사를 필수로 적용하고, `main` 보호 규칙에서
최신 base branch 반영을 요구하는 strict 설정을 사용한다.

`CLOUD_PR_TOKEN`은 Cloud 저장소에서 사용할 수 있는 Actions 시크릿으로 등록한다.
기준선 갱신 job은 Environment를 사용하지 않으므로 Environment에만 저장한
시크릿은 이 job에 전달되지 않는다. 토큰에는 기준선 브랜치 push와 PR 생성에
필요한 Contents 및 Pull requests 쓰기 권한을 부여한다. Actions의 기본 토큰은 GitHub 읽기 권한을 사용한다.

## 운영 앱 확인

| 구분 | 대상 | Argo CD Application | release | Namespace |
|---|---|---|---|---|
| backend | auth-api | auth-api-prod | auth-api | auth-prod |
| backend | chat-api | chat-api-prod | chat-api | chat-prod |
| backend | chat-ws | chat-ws-prod | chat-ws | chat-prod |
| backend | travel-api | travel-api-prod | travel-api | travel-prod |
| backend | travel-worker | travel-worker-prod | travel-worker | travel-prod |
| backend | general-api | general-api-prod | general-api | general-prod |
| backend | general-ws | general-ws-prod | general-ws | general-prod |
| ai | match-ai | match-ai | match-ai | ai-prod |
| ai | travel-ai | travel-ai | travel-ai | ai-prod |
| ai | travel-ai-worker | travel-ai-worker | travel-ai-worker | ai-prod |
