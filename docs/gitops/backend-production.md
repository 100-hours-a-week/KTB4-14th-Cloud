# BE 수동 운영 승격

운영 승격은 `backend-production.yml`을 **main에서** 수동 실행한다. 기본 `preview`에서 대상·누적 근거 경로·이전 SHA/digest·후보 SHA/digest를 확인한 후, 같은 입력으로 `pr`을 실행한다. 이미지를 재빌드하지 않는다. 이 workflow는 Sync하거나 기준선을 갱신하지 않는다.

## 후보와 사람의 검증

기존 `backend_staging.execute(validate_only=True)`의 run attempt·정확한 artifact·registry ARM64 검증을 재사용한다. Cloud main의 staging-backend-candidate.json 전체와 스테이징 API/Socket tag·digest가 모두 일치해야 한다. candidate 기록은 **Git 반영 기록**이다. 확인자는 실제 두 Deployment의 digest, 확인 시각·이름, 로그인·REST·WS 등 수행 결과를 입력하고 확인 체크를 해야 한다. 입력은 사람의 진술이며 클러스터 자동 증명이 아니다. 스테이징이 먼저 다른 후보로 이동했으면 중단하고 검증부터 다시 한다.

## 실제 배포 기준선 초기화 (현재는 의도적으로 없음)

`deploy/backend-production-baseline.json`은 운영 담당자가 실제 Sync 후 대상별 실행 이미지와 기능을 확인하고 별도 리뷰 PR로 작성한다. 예시 SHA/digest를 실제 값으로 복사하지 않는다. 각 Pod가 같은 버전인지 확인하고 rollout 중이면 기록하지 않는다. 소스 SHA는 검증된 빌드 run/artifact로 실행 digest와 연결한다. image tag가 Git values에 없다면 digest 일치와 사람의 SHA 연결 확인을 사용한다.

형태는 schema_version: 1 및 deployments 객체이다. deployments는 다음 **7개 키 모두** 필요하다: auth-api, chat-api, chat-ws, travel-api, travel-worker, general-api, general-ws. 각 항목에는 source_sha(전체 40자리), image_digest(sha256), confirmed_by, confirmed_at(UTC ISO 8601), evidence(실제 digest·기능 확인 결과 링크)를 기록한다. 추정값이 든 초기화 파일은 제공하지 않는다.

Git 운영 values의 digest와 기준선이 다르거나, tag가 있으면서 기준선 SHA와 다르면 진행 중 배포/불일치로 중단한다. 기준선은 PR 생성·병합 시 바꾸지 않는다. 기존 운영 승격 PR이 열려 있으면 새 PR을 만들지 않는다. 기준 SHA가 없거나 후보의 조상이 아니면 중단한다. 전체 Git 이력의 `git diff --no-renames -z`로 삭제와 rename 양쪽을 평가한다.

## 기능 매핑 근거

검토한 BE source: `src/main/java/com/audigo/domain/` (이번 로컬 BE 작업 트리). 네 기능은 auth / chat / travel / general이며 REST와 WS를 묶는다. `scripts/backend_production.py:classify`가 실제 실행 규칙이다. 단일 통합 이미지에서는 이름만으로 역할이 격리되지 않으므로 공유 코드 규칙은 보수적이다.

| 경로 | 대상 | 확인한 코드 의존 근거 |
|---|---|---|
| domain/auth, domain/user | 전체 7개 | auth/service/AuthService, TokenService가 User/Repository 사용. user/service/UserService는 TravelPlanRepository 사용. 공통 인증·사용자 계약 영향 보수 처리 |
| domain/chat | 채팅 + 일반 | chat/service/ChatMessageService → NotificationService; matching/service/MatchingChatRoomService → ChatRoomPreparationService |
| domain/travel | 여행 + 일반 | matching 코드가 여행 테마 타입 사용. 여행 API/worker는 같은 SQS 조건 사용 |
| domain/matching, domain/notification | 전체 7개 | MatchingCandidateService → NotificationService/User, MatchingChatRoomService → chat. 여러 도메인 알림 발행 측의 계약 변경을 보수 처리 |
| global, resources, Gradle, Dockerfile, workflow, 미분류 | 전체 7개 | 실행 설정/의존성/보안 및 알 수 없는 실행 경로 |
| docs/, src/test/, 저장소 최상위 *.md | 없음 | 문서 및 격리된 테스트 전용 경로. 빌드/설정 파일은 제외하지 않음 |

대상마다 실제 배포 SHA부터 누적 비교하므로 이전에 미배포한 실행 변경도 남는다. 이름 변경은 구/신 경로 모두 포함한다. 채팅·여행·일반 중 하나가 선택되면 쌍 전체를 갱신한다. 다른 기능으로의 import가 추가되면 이 매핑도 재검토해야 한다. 세분화 전까지 불필요한 전체 갱신을 허용해 누락을 줄인다.

## 권한과 보호 정책

BE_READ_TOKEN은 기존 BE Actions/Contents 읽기 권한만 유지한다. 새 CLOUD_PR_TOKEN은 별도 Cloud 저장소 전용 GitHub App installation token 또는 최소 범위 토큰이며 Contents write, Pull requests write가 필요하다. workflow의 GITHUB_TOKEN은 read-only이다. GitHub App 토큰을 사용한다면 실행 전에 안전한 발급 단계와 별도 App ID/private key 구성이 필요하다. 현재 workflow는 이미 발급된 유효 토큰을 secrets.CLOUD_PR_TOKEN으로 받는 계약이며 App을 생성하거나 설치하지 않는다. 단기 토큰을 정적 secret으로 운영하지 말고 실제 인증 방식을 확정한 뒤 발급 단계를 구성해야 한다.

main 직접 push·리뷰 우회 권한을 주지 않는다. main 필수 리뷰/필수 검증과 CODEOWNERS로 workflow·검증 코드·기준선을 보호한다. 토큰만으로 파일 경로 제한이 보장되지는 않으므로 PR diff 허용 파일 및 image.tag/digest 전용 검증을 필수 상태 검사로 연결해야 한다. 기존 토큰에 쓰기를 추가하지 않는다. 자동 병합을 설정하지 않는다.

## 배포/롤백

PR의 대상별 근거와 이전 SHA/digest(롤백 버전)를 확인한다. 병합 후 Cloud 팀이 각 Application을 수동 Sync하고 Pod 실제 이미지·Ready·DB·Redis 및 해당 기능을 확인한다. 여행은 스테이징 SQS/Worker 미검증을 명시하며 운영에서 generation_job_id로 발행·AI 소비·결과 소비·완료 및 중복/재시도 영향을 추적한다. 실패 시 이전 SHA tag와 digest로 values를 복구하고 수동 Sync한다. 성공 후 별도 PR로 기준선을 갱신한다. 초기 기준선, 실제 스테이징 검증, PR 쓰기 인증과 보호 정책이 준비되기 전에는 운영 승격이 차단되는 것이 정상이다.

미리보기에는 Cloud 쓰기 토큰을 전달하지 않는다. PR 모드는 고정 `backend-production/pending` 브랜치를 사용해 동일 base/head의 중복 PR을 방지한다. 기존 브랜치에 non-fast-forward push가 필요하면 force 없이 중단한다. 이전 제안이 닫혔지만 브랜치가 남아 충돌하는 경우 담당자가 이력을 검토하고 정리한 후 재실행한다. 이 workflow의 concurrency는 다른 수동 도구 실행을 잠그지는 않으며, 조회 직후의 외부 PR 생성 경쟁은 GitHub의 동일 head/base 중복 방지와 일반 push 충돌로 방어한다.
