# AI 작업/결과 SQS 신규 구축

Audigo V2 여행 생성 비동기 처리용 SQS를 IaC로 만드는 절차입니다.

```text
Backend ──SendMessage──▶ Audigo-AI-Job-Queue ──Receive/Delete──▶ AI Worker
Backend ◀─Receive/Delete── Audigo-AI-Result-Queue ◀──SendMessage── AI Worker
```

## 상태

| 항목 | 상태 |
|---|---|
| `cloudformation/sqs.yaml` 작성 | 완료 |
| `bootstrap/infra-iam.yaml`의 Platform 역할에 SQS·알람 권한 추가 | 완료 (관리자 적용 필요) |
| cfn-lint, 배포 설정 검증, 테스트 | 통과 (78개) |
| `infra-iam` bootstrap 스택 업데이트 | **미완료** |
| CREATE Change Set·AWS 리소스 생성 | **미완료** |
| 최초 생성 후 `allow_create=false` 복원 | **미완료** |
| Backend·AI Worker Task Role·환경 변수 | 별도 작업 |

## 파일 구성

| 파일 | 배포 주체 | 내용 |
|---|---|---|
| `cloudformation/sqs.yaml` | 인프라 CD (`audigo-sqs`, Platform) | Queue 2개, 알람 6개 (DLQ 없음) |
| `deploy/parameters/sqs.json` | 인프라 CD | Visibility Timeout, SNS Topic |
| `bootstrap/infra-iam.yaml` | 관리자 수동 | Platform 실행 역할에 `Audigo-AI-*` Queue, `audigo-sqs-*` 알람 권한 |
| `tests/test_infra_sqs.py` | CI | 설정값·DLQ 없음·Retain·배포 IAM 범위 검증 |

## 생성할 리소스

### Queue

| 항목 | Job Queue | Result Queue |
|---|---|---|
| 이름 | `Audigo-AI-Job-Queue` | `Audigo-AI-Result-Queue` |
| 유형 | Standard | Standard |
| Producer → Consumer | Backend → AI Worker | AI Worker → Backend |
| Long Polling | 20초 | 20초 |
| Visibility Timeout | 360초 (파라미터) | 60초 (파라미터) |
| 보존 기간 | 4일 (345600) | 4일 (345600) |
| 최대 메시지 크기 | 1MiB (1048576) | 1MiB (1048576) |
| 암호화 | SSE-SQS | SSE-SQS |

DLQ와 RedrivePolicy는 두지 않습니다. 이유는 아래 [재처리 정책](#재처리-정책)을 참고합니다.

### CloudWatch 알람

| 알람 이름 | 지표 | 기본 임계값 |
|---|---|---|
| `audigo-sqs-ai-job-visible` | MessagesVisible | > 50, 5분 지속 |
| `audigo-sqs-ai-job-oldest-age` | AgeOfOldestMessage | > 600초 |
| `audigo-sqs-ai-job-not-visible` | MessagesNotVisible | > 20 |
| `audigo-sqs-ai-result-visible` | MessagesVisible | > 20 |
| `audigo-sqs-ai-result-oldest-age` | AgeOfOldestMessage | > 120초 |
| `audigo-sqs-ai-result-not-visible` | MessagesNotVisible | > 20 |

- 모든 알람은 1분 주기, 최댓값 기준, 5회 연속 초과 시 발생하며 `TreatMissingData=notBreaching`입니다.
- `AlarmTopicArn`을 비워 두면 알람 상태만 기록합니다. 알림이 필요하면 SNS Topic ARN을 파라미터로 넣습니다. SNS Topic 생성은 `monitoring.yaml` 범위입니다.

모든 리소스는 `DeletionPolicy/UpdateReplacePolicy: Retain`입니다.

## 적용 순서

1. **관리자: 배포 IAM 갱신**
   `bootstrap/infra-iam.yaml` 스택을 Change Set으로 업데이트합니다. 변경 내용은 다음과 같습니다.
   - GitHub 역할: `audigo-sqs` 스택의 조회, Platform Change Set 생성, 실행 범위
   - Platform 실행 역할: `AudigoPlatformSqs` 정책
2. **PR 병합 → 인프라 CD**
   `audigo-sqs`는 `enabled=true`, `allow_create=true`라서 CREATE Change Set이 계획됩니다. Change Set을 검토하고 `iac-production` 승인 후 실행합니다.
3. **`allow_create=false` 복원**
   `CREATE_COMPLETE`를 확인한 후 별도 PR로 되돌립니다. DB와 같은 절차입니다.

> 1번이 끝나기 전에 2번을 병합하면 CreateChangeSet 권한이 없어 CD 계획이 실패합니다.

## 후속 작업 (별도 PR)

Backend/AI Worker Task Role과 Task Definition은 이 스택의 범위가 아닙니다. 별도 작업에서 아래 요청 사항을 반영합니다.

| 역할 | Job Queue | Result Queue | 두 Queue |
|---|---|---|---|
| Backend Task Role | `SendMessage` | `ReceiveMessage`, `DeleteMessage`, `ChangeMessageVisibility` | `GetQueueAttributes` |
| AI Worker Task Role | `ReceiveMessage`, `DeleteMessage`, `ChangeMessageVisibility` | `SendMessage` | `GetQueueAttributes` |

| 환경 변수 | Backend | AI Worker |
|---|---|---|
| `AWS_REGION` | `ap-northeast-2` | `ap-northeast-2` |
| `AUDIGO_AI_JOB_QUEUE_URL` | `audigo-sqs` 출력 `AiJobQueueUrl` | 동일 |
| `AUDIGO_AI_RESULT_QUEUE_URL` | `audigo-sqs` 출력 `AiResultQueueUrl` | 동일 |

- 현재 서비스는 퍼블릭 서브넷과 퍼블릭 IP로 SQS에 접근합니다. 프라이빗 서브넷으로 옮길 때는 NAT 또는 SQS Interface VPC Endpoint가 필요합니다.

## 확인 명령

```bash
aws cloudformation describe-stacks --stack-name audigo-sqs --query 'Stacks[0].Outputs'
aws sqs get-queue-attributes --queue-url <AiJobQueueUrl> --attribute-names All
aws cloudwatch describe-alarms --alarm-name-prefix audigo-sqs-
```

## 재처리 정책

2026-10-18 확정 사항입니다.

| 항목 | 결정 | 구현 시 지켜야 할 것 |
|---|---|---|
| Job 재시도 | 큐 단 재시도 없음. AI Worker 내부에서 재시도 | 최종 실패 시에도 실패 결과를 Result Queue로 보내고 **Job 메시지를 삭제**합니다 |
| Job Visibility Timeout | 360초 | 기준: `1회 생성 최대 시간 × (내부 재시도 + 1) + 여유`. 넘을 수 있으면 Worker가 `ChangeMessageVisibility`로 연장합니다. 값 변경은 `sqs.json`만 수정합니다 (교체 없음) |
| Result 처리 실패 | DLQ 없음. Backend 타임아웃 장치로 처리 | 처리 실패 메시지도 **삭제**합니다 |
| 1MiB 초과 결과 | 아직 도입하지 않음 | 도입 시 버킷 생성 + Task Role에 Worker `s3:PutObject`, Backend `s3:GetObject` 추가 |

- DLQ가 없으므로 삭제하지 않은 메시지는 Visibility Timeout마다 다시 보이며 보존 기간(4일) 동안 반복 수신됩니다. `*-not-visible`, `*-oldest-age` 알람이 이 상황을 잡는 용도입니다.

Standard Queue는 같은 메시지를 2번 이상 전달할 수 있으므로 Backend와 Worker 모두 `jobId` 기준으로 멱등 처리해야 합니다.
