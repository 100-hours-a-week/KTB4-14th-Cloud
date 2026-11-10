# 운영 배포 기준선 갱신

이 절차는 운영 승격 PR이 병합되고 Argo CD에서 **수동 Sync**된 뒤,
실제로 실행 중인 이미지와 기능을 확인해 기준선 PR을 만드는 절차다. 이
workflow는 Sync하거나 Kubernetes·Argo CD에 접속하지 않는다. Cloud 팀은
생성된 기준선 PR을 검토하고 수동 병합한다.

## 실행 전 운영 확인

대상 앱을 수동 Sync한 뒤 rollout이 끝날 때까지 기다린다. 다음 명령은
운영 접근 권한을 가진 담당자가 읽기 전용으로 실행해 관측값을 수집하는
예시다. Actions에는 이 접근 권한이나 kubeconfig를 추가하지 않는다.

```sh
# Argo CD: 앱별 Sync/Health와 적용 revision을 확인한다.
argocd app get <application> --grpc-web -o json

# Kubernetes: rollout, Ready 상태와 imageID를 확인한다.
kubectl -n <namespace> rollout status deployment/<deployment> --timeout=5m
kubectl -n <namespace> get pods -l app.kubernetes.io/instance=<release> -o json
```

각 Pod가 Ready이고 삭제 중이 아니며, runtime `imageID`가 승격 후보와
일치해야 한다. registry index digest와 플랫폼별 runtime digest가 다른
경우에는 아래 `image_index` 방식으로 매핑을 검증한다. 매칭, 여행 생성,
Worker 큐 소비 같은 기능 검증은 담당자가 수행하고 결과를
`functional_evidence`에 기록한다.

## workflow_dispatch 입력

`Update production baseline`은 Cloud 저장소 `main`에서만 실행한다.

- `service`: `backend` 또는 `ai`
- `promotion_pr`: 실제로 병합된 동일 Cloud 저장소 운영 승격 PR 번호
- `observations`: 아래 구조의 JSON. 승격 PR의 대상 앱만 정확히 넣는다.
- `functional_evidence`: 비어 있지 않은 기능 검증 근거. 검증하지 않은
  기능을 성공으로 기록하지 않는다.

`observations` 예시는 다음과 같다. `revision`은 40자리 Cloud commit SHA이며,
`image_id`는 Pod runtime imageID다.

```json
{
  "apps": {
    "travel-ai": {
      "application": "travel-ai",
      "namespace": "ai-prod",
      "sync_status": "Synced",
      "health_status": "Healthy",
      "revision": "<40-char Cloud commit SHA>",
      "rollout_complete": true,
      "image_index": "<raw OCI/Docker index JSON string when imageID is platform digest>",
      "pods": [
        {
          "ready": true,
          "deleting": false,
          "image_id": "<registry>/<repository>@sha256:<64 hex>"
        }
      ]
    }
  }
}
```

runtime `imageID`가 후보 index digest가 아닌 linux/arm64 플랫폼 digest라면,
담당자는 registry 읽기 권한으로 다음처럼 후보 index의 원문을 수집해
`image_index`에 넣는다. 이 값은 Actions가 registry에 접속하기 위해 쓰지
않으며, script가 UTF-8 원문 바이트를 SHA-256으로 해시해 후보 digest와
대조한다. 도구가 덧붙인 줄바꿈 하나만 제거한 바이트도 후보 digest와
일치할 때 허용한다. index의 linux/arm64 descriptor digest는 runtime
`imageID`와 일치해야 한다.

```sh
docker buildx imagetools inspect <repository>@<candidate-index-digest> --raw > image-index.json
```

workflow는 입력 SHA나 digest를 기준선에 직접 쓰지 않는다. 병합된 운영
승격 PR의 허용된 values 변경, 신뢰할 수 있는 후보·발행 기록, 최신 `main`
values를 대조해 `source_sha`와 `image_digest`를 확정한다. 대상 앱 하나라도
관측값이 없거나 Sync/rollout/Pod/imageID가 맞지 않으면 PR을 만들지 않는다.

## 재실행과 수동 병합

같은 운영 승격 PR로 다시 실행하면 열린 기준선 PR을 재사용하고 링크를
Summary에 기록한다. 이미 기준선이 반영되었으면 성공으로 종료한다. 실행
중에 운영 values가 바뀌거나 롤백되면 기준선 PR은 오래된 것으로 판단되어
검사에서 실패해야 한다. 기준선 PR의 필수 검사는 최신 `main`과 PR 변경이
현재 운영 values에 여전히 대응하는지 확인해야 한다.

기준선 PR은 자동 병합하지 않는다. Cloud 팀이 최신 브랜치와 필수 검사를
확인한 뒤 수동으로 병합한다. 이 PR의 병합도 Argo CD Sync, 이미지 갱신,
배포 완료를 의미하지 않는다.

## 필요한 GitHub 설정

`CLOUD_PR_TOKEN`은 기준선 전용 branch push 및 main 대상 PR 생성에 필요한
최소 Contents/Pull requests write 권한만 가져야 한다. workflow는 이 token을
PR 생성 단계에만 전달하며, 기본 `GITHUB_TOKEN`은 읽기 전용으로 유지한다.
Actions에는 registry·Kubernetes·Argo CD 자격증명을 추가하지 않는다.

기준선 PR에는 `Production baseline freshness` 검사를 필수로 설정하고,
main 보호 규칙은 최신 base branch 반영을 요구하는 strict 상태로 설정해야
한다. Cloud 팀의 수동 리뷰도 유지한다. 이 workflow는 저장소 보호 설정을
직접 변경하지 않는다.

## 구현 병합 뒤 최초 실행 예시

아래 명령은 이 구현이 `main`에 병합된 뒤 담당자가 **실제 운영 검증을
마친 경우에만** 실행하는 예시다. 예시 파일은 비어 있는 placeholder이며,
이 문서가 기능 검증이나 운영 상태를 주장하지 않는다.

```sh
# backend-observations.json 및 backend-functions.txt는 실제 확인 뒤 작성한다.
gh workflow run production-baseline.yml --ref main \
  -f service=backend \
  -f promotion_pr=83 \
  -F observations=@backend-observations.json \
  -F functional_evidence=@backend-functions.txt

# ai-observations.json 및 ai-functions.txt는 실제 확인 뒤 작성한다.
gh workflow run production-baseline.yml --ref main \
  -f service=ai \
  -f promotion_pr=89 \
  -F observations=@ai-observations.json \
  -F functional_evidence=@ai-functions.txt
```

`confirmed_by`는 workflow를 실행한 GitHub actor로, `confirmed_at`은 workflow가
기록하는 UTC 시각으로 정해진다. 기준선 PR의 `Production baseline freshness`
검사와 strict 최신 main 반영 요구, 수동 리뷰가 통과하기 전에는 병합하지
않는다.

### 대상 매핑

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

### 담당자 로컬 관측 수집 예시

다음은 운영 접근 권한을 가진 담당자가 로컬에서만 실행할 수 있는 읽기 전용
수집 함수다. `argocd app get`, `kubectl rollout status`, Deployment와 Pod의
JSON 조회만 사용한다. container 이름은 `app`, Pod 선택자는
`app.kubernetes.io/instance=<release>`를 사용한다. 모든 Deployment의
`observedGeneration` 반영 및 available/ready replicas 충족 여부를
`rollout_complete`로 기록한다.

```sh
collect_observation() {
  target=$1 app=$2 release=$3 namespace=$4
  argocd_json=$(argocd app get "$app" --grpc-web -o json)
  # rollout status는 읽기 전용 대기이며, 실패하면 함수를 종료한다.
  kubectl -n "$namespace" rollout status "deployment/$release" --timeout=5m >&2 || return
  deployment=$(kubectl -n "$namespace" get "deployment/$release" -o json)
  pods=$(kubectl -n "$namespace" get pods \
    -l "app.kubernetes.io/instance=$release" -o json)
  app_status=$(printf '%s' "$argocd_json" | jq -r '.status.sync.status')
  app_health=$(printf '%s' "$argocd_json" | jq -r '.status.health.status')
  revision=$(printf '%s' "$argocd_json" | jq -r '.status.sync.revision')
  rollout_complete=$(printf '%s' "$deployment" | jq '
    .status.observedGeneration == .metadata.generation and
    (.status.updatedReplicas // 0) == (.spec.replicas // 1) and
    (.status.availableReplicas // 0) == (.spec.replicas // 1) and
    (.status.readyReplicas // 0) == (.spec.replicas // 1)')
  jq -n \
    --arg target "$target" --arg application "$app" --arg namespace "$namespace" \
    --arg sync "$app_status" --arg health "$app_health" --arg revision "$revision" \
    --argjson rollout_complete "$rollout_complete" --argjson pods "$pods" '
    {($target): {
      application: $application,
      namespace: $namespace,
      sync_status: $sync,
      health_status: $health,
      revision: $revision,
      rollout_complete: $rollout_complete,
      pods: [$pods.items[] | {
        ready: (any(.status.conditions[]?; .type == "Ready" and .status == "True")),
        deleting: (.metadata.deletionTimestamp != null),
        image_id: ((.status.containerStatuses[]? | select(.name == "app") | .imageID) // "")
      }]
    }}}
}

# 예: 각 결과를 배열로 모아 apps 객체로 합친다. 실제 대상만 넣는다.
# collect_observation auth-api auth-api-prod auth-api auth-prod > auth-api.json
# jq -s '{apps: add}' auth-api.json chat-api.json > backend-observations.json
```

AI의 runtime imageID가 index digest가 아닌 플랫폼 digest이면, 앞 절의 raw
index 수집도 수행해 해당 앱 관측값에 `image_index`를 추가한다. 수집 결과와
기능 검증 근거는 실제 실행 전에는 생성하거나 제출하지 않는다.
