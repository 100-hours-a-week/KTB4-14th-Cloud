# AI Kubernetes 운영 승격

`ai-production.yml`은 성공한 Cloud 스테이징 갱신 결과 artifact를 다시
검증한 뒤 운영 후보를 미리보기합니다. `workflow_dispatch`는 이미 검증한
Cloud 스테이징 run ID와 attempt를 지정할 때만 사용합니다. 두 경로 모두
`AI 스테이징 이미지 갱신`의 성공한 `main` 실행과
`staging-ai-result-<cloud-run-id>-<attempt>` artifact를 대조합니다.

## 운영 기준선

자동 승격은 `deploy/ai-production-baseline.json`이 있어야 합니다. 이 파일은
실제 수동 Sync와 Pod 확인 뒤 사람이 별도 PR로 등록·갱신합니다. 승격 PR은
기준선을 변경하지 않습니다.

```json
{
  "schema_version": 1,
  "deployments": {
    "match-ai": {
      "source_sha": "<40-char mockAI SHA>",
      "image_digest": "sha256:<64 hex>",
      "confirmed_by": "<verifier>",
      "confirmed_at": "<UTC ISO-8601 timestamp>",
      "evidence": "<Pod digest, Ready and functional-check evidence>"
    },
    "travel-ai": { "source_sha": "...", "image_digest": "...", "confirmed_by": "...", "confirmed_at": "...", "evidence": "..." },
    "travel-ai-worker": { "source_sha": "...", "image_digest": "...", "confirmed_by": "...", "confirmed_at": "...", "evidence": "..." }
  }
}
```

기준선 digest와 Git 운영 values digest가 다르면 이전 Sync 또는 기준선 등록이
끝나지 않은 것으로 보고 새 PR을 만들지 않습니다. 열린 `ai-production/` PR도
새 승격을 차단합니다.

## 승인과 배포

미리보기는 후보 SHA·digest 및 세 Application(`match-ai`, `travel-ai`,
`travel-ai-worker`)의 변경 전 digest를 Summary에 표시합니다. 대상이 있으면
`ai-production` Environment의 필수 reviewer 승인 뒤 동일 artifact·기준선·values
snapshot을 재검증하고 PR을 생성합니다. 자동 병합은 요청하지 않습니다.

병합 후에도 Argo CD Sync는 수동입니다. 스테이징 여행 생성·매칭 API 검증은
승인자가 별도로 확인해야 하며, 운영 `travel-ai-worker`는 수동 Sync 뒤 SQS
작업 소비와 결과 저장/발행을 반드시 확인해야 합니다. 확인 뒤에만 실제 배포
근거를 기준선 PR에 기록합니다.

## 필요한 GitHub/AWS 설정

- `ai-production` Environment에 필수 reviewer를 설정하고 `CLOUD_PR_TOKEN`을
  그 Environment secret으로 둡니다. 토큰은 main 대상 PR 생성·branch push 권한만
  가져야 합니다.
- `AI_READ_TOKEN`은 mockAI Actions run/artifact 읽기 권한이 필요합니다.
- `AWS_ROLE_ARN` OIDC 신뢰 정책은 preview job의
  `repo:100-hours-a-week/KTB4-14th-Cloud:ref:refs/heads/main` subject와, Environment
  승인 job의 기본 subject인
  `repo:100-hours-a-week/KTB4-14th-Cloud:environment:ai-production`를 각각 허용해야
  합니다. 조직의 subject 커스터마이징을 사용한다면 실제 claim에 맞춰 두 job을
  명시적으로 허용합니다. 역할에는 후보 ECR 저장소의 `ecr:DescribeImages`와 registry
  manifest 조회에 필요한 ECR 인증 권한이 필요합니다. 권한·OIDC·이미지 검증 실패는
  values를 갱신하지 않고 실패합니다.
