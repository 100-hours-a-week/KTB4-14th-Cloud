# 세션 캐시(ElastiCache Serverless Valkey) 신규 구축

Spring Session 저장소로 쓰는 ElastiCache Serverless(Valkey)를 IaC로 만드는 절차입니다.

```text
App(sg-0da962a4706dacd5f, sg-079261f4850dacc68) ──TLS 6379/6380, AUTH audigo <password>──▶ audigo-session (Private Subnet A/B)
```

## 상태

| 항목 | 상태 |
|---|---|
| `cloudformation/cache.yaml` 작성 | 완료 |
| `audigo/v1/backend`에 `REDIS_PASSWORD` 추가, 사용자 `audigo` 수동 생성 | 대기 (관리자) |
| `bootstrap/infra-iam.yaml` Platform 역할 `AudigoPlatformCache`, GitHub 역할 스택 범위 추가 | 완료 (관리자 적용 필요) |
| cfn-lint, 배포 설정 검증, 테스트 | 통과 |
| CREATE Change Set·AWS 리소스 생성 | 대기 |
| 최초 생성 후 `allow_create=false` 복원 | 대기 |
| Backend Task Definition 환경 변수 | 별도 작업 (BE 레포) |

## 리소스와 관리 주체

| 리소스 | 이름 | 설정 | 관리 |
|---|---|---|---|
| Serverless Cache | `audigo-session` | Valkey 8, 스토리지 최대 1GB, ECPU 한도 없음, 스냅샷 보관 0 | 인프라 CD (`audigo-cache`) |
| 보안 그룹 | `audigo-elasticache-sg` | 인바운드: 앱 SG 2개 → TCP 6379-6380 / 아웃바운드 없음 | 인프라 CD |
| 사용자 그룹 | `audigo-session-users` | `audigo-default-off`, `audigo` | 인프라 CD |
| 사용자 | `audigo-default-off` (UserName `default`) | `off -@all`. 무인증 접속 차단 | 인프라 CD |
| 사용자 | `audigo` | 비밀번호 인증, `on ~* +@all` | 관리자 수동 |
| Secret 키 | `audigo/v1/backend` → `REDIS_PASSWORD` | 기존 앱 공용 Secret에 키 추가 | 관리자 수동 |

- 비밀번호는 IaC가 읽거나 다루지 않습니다. 사용자 `audigo`와 비밀번호(`audigo/v1/backend`의 `REDIS_PASSWORD`)는 관리자가 직접 관리하고, IaC는 사용자 그룹에서 사용자 ID만 참조합니다. Platform 역할은 `audigo` 사용자를 수정할 수 없습니다.
- 전송 중(TLS)·저장 시 암호화는 Serverless에서 항상 켜져 있습니다.
- 스토리지 한도(1GB)에 도달하면 쓰기가 거부됩니다. 세션 TTL을 반드시 설정합니다.

## 적용 순서

1. **관리자: 배포 IAM 갱신** — `bootstrap/infra-iam.yaml` 스택을 Change Set으로 업데이트합니다.
2. **관리자: 비밀번호 추가** — Secrets Manager 콘솔 → `audigo/v1/backend` → 보안 암호 값 편집에서 키 `REDIS_PASSWORD`, 값=직접 정한 비밀번호를 추가합니다. 기존 키는 그대로 둡니다. 비밀번호 제약: 16~128자, 공백·`"`·`/`·`@` 불가.
3. **관리자: 사용자 생성** — ElastiCache 콘솔 → 사용자 관리 → 사용자 생성: ID·이름 `audigo`, 엔진 Valkey, 인증 방식 비밀번호(2번 값), 액세스 문자열 `on ~* +@all`. CLI를 쓰면 비밀번호가 셸 기록에 남지 않게 합니다.
   ```bash
   read -rs CACHE_PW
   aws elasticache create-user --user-id audigo --user-name audigo --engine valkey \
     --access-string 'on ~* +@all' --passwords "$CACHE_PW"
   unset CACHE_PW
   ```
4. **PR 병합 → 인프라 CD** — `audigo-cache` CREATE Change Set을 검토하고 `iac-production` 승인으로 실행합니다.
5. **`allow_create=false` 복원** — `CREATE_COMPLETE` 확인 후 별도 PR로 되돌립니다.

> 3번 전에 4번을 실행하면 사용자 `audigo`가 없어 사용자 그룹 생성이 실패합니다.

## Backend 연동 (BE 레포 Task Definition)

| 이름 | 종류 | 값 |
|---|---|---|
| `SPRING_DATA_REDIS_HOST` | environment | `audigo-cache` 출력 `SessionCacheEndpoint` |
| `SPRING_DATA_REDIS_PORT` | environment | `6379` |
| `SPRING_DATA_REDIS_SSL_ENABLED` | environment | `true` |
| `SPRING_DATA_REDIS_USERNAME` | environment | `audigo` |
| `SPRING_DATA_REDIS_PASSWORD` | secrets | `arn:aws:secretsmanager:ap-northeast-2:185489190599:secret:audigo/v1/backend-dTTCJ5:REDIS_PASSWORD::` |

- 실행 역할 `audigo-ecs-task-execution-role`은 이미 `audigo/v1/backend`를 읽으므로 IAM 변경이 없습니다. 다른 실행 역할을 쓰는 앱이면 그 역할에 이 Secret 조회 권한이 있는지 확인합니다.
- ElastiCache는 `CONFIG` 명령을 막으므로 `ConfigureRedisAction.NO_OP`을 등록하고, 기본 `RedisSessionRepository`(TTL 기반)를 씁니다.
- Serverless는 클러스터 모드로 동작합니다. 여러 키를 다루는 명령은 같은 슬롯(해시태그)이어야 합니다.

## 비밀번호 교체

1. ElastiCache 사용자에 새 비밀번호를 추가합니다(사용자는 비밀번호를 최대 2개 가질 수 있어 무중단 교체가 됩니다).
   콘솔의 사용자 수정에서 기존·신규 비밀번호를 함께 둡니다(CLI: `modify-user --user-id audigo --passwords <기존> <신규>`).
2. `audigo/v1/backend`의 `REDIS_PASSWORD`를 신규 비밀번호로 바꾸고 앱 태스크를 재시작합니다.
3. 사용자에서 기존 비밀번호를 제거합니다.

## 확인 명령

```bash
aws cloudformation describe-stacks --stack-name audigo-cache --query 'Stacks[0].Outputs'
aws elasticache describe-serverless-caches --serverless-cache-name audigo-session
aws elasticache describe-users --user-id audigo
```
