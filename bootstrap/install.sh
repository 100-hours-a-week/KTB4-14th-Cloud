#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
values_file="$script_dir/argocd/values.yaml"
manifest="$(mktemp)"
trap 'rm -f -- "$manifest"' EXIT

kubectl_cmd=(
  sudo kubectl
  --kubeconfig=/etc/kubernetes/admin.conf
  --request-timeout=30s
)

# 적용 전에 차트를 렌더링한다.
helm template argocd argo/argo-cd \
  --version 10.10.3 \
  --namespace argocd \
  --kube-version 1.36.5 \
  --include-crds \
  --values "$values_file" \
  >| "$manifest"

# argocd 네임스페이스가 없으면 생성한다.
namespace="$("${kubectl_cmd[@]}" get namespace argocd \
  --ignore-not-found -o name)"

if [[ -z "$namespace" ]]; then
  "${kubectl_cmd[@]}" create namespace argocd
fi

# API 서버에서 적용 가능 여부를 먼저 검사한다.
"${kubectl_cmd[@]}" -n argocd apply \
  --server-side \
  --dry-run=server \
  -f "$manifest" > /dev/null

echo "ARGOCD_SERVER_DRY_RUN_OK"

# 검증된 설정을 적용한다.
"${kubectl_cmd[@]}" -n argocd apply \
  --server-side \
  -f "$manifest"

echo "ARGOCD_APPLY_OK"
