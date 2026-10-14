# PR·커밋 및 Actions 실행 제목 컨벤션

## PR·커밋

`type(scope): 한글 변경 설명` 형식을 사용한다. PR 제목과 최종 병합 커밋 제목을 동일하게 작성한다.

| 구분 | 예시 |
|---|---|
| 백엔드 릴리즈 | `release(backend): v2.1.0 배포` |
| AI 릴리즈 | `release(ai): v2.1.0 배포` |
| 프론트 릴리즈 | `release(frontend): v2.1.0 배포` |
| 인프라 추가 | `feat(infra/network): 프라이빗 서브넷 추가` |
| 배포 설정 | `ci(infra/dns): DNS 스택 CD 활성화` |
| 오류 수정 | `fix(infra/iam): 역할 설명 문자 제약 수정` |
| 파이프라인 변경 | `ci(infra): CI/CD 실행 제목 정리` |
| 문서 변경 | `docs(infra): IAM 운영 절차 정리` |

앱 릴리즈 버전은 실제 배포 이미지의 릴리즈 버전과 일치시킨다. AI도 기존 이미지 버저닝을 사용하며 별도의 버전 번호를 만들지 않는다. 표의 버전은 예시이며 실제 이미지 태그 체계를 변경하지 않는다.

## Actions 실행 제목

실행자는 GitHub가 별도로 표시하므로 `run-name`에 사용자 이름이나 언급을 넣지 않는다.

| 실행 | 표시 예시 |
|---|---|
| 인프라 PR CI | `[INFRA CI] feat(infra/network): 프라이빗 서브넷 추가` |
| 인프라 main CI | `[INFRA CI] ci(infra): CI/CD 실행 제목 정리` |
| 인프라 수동 CI | `[INFRA CI] 수동 검증` |
| 인프라 자동 CD | `[INFRA CD] 활성 스택 전체` |
| 인프라 수동 CD | `[INFRA CD] audigo-dns` |
| 백엔드 CD (권장) | `[APP CD][Backend] v2.1.0` |
| AI CD (권장) | `[APP CD][AI] v2.1.0` |
| 프론트 CD (권장) | `[APP CD][Frontend] v2.1.0` |

인프라 워크플로에만 실행 제목을 적용했다. 앱 워크플로 적용은 별도 작업이다. 제목은 표시용이며 배포 대상은 경로 필터와 스택 설정으로 결정한다. 제목 컨벤션을 강제하는 PR 검증은 아직 구현하지 않았다.
