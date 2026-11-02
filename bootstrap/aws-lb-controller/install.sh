#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v helm >/dev/null || { echo "helm 미설치"; exit 1; }

CHART_VERSION="3.6.2"

helm repo add lb-controller https://aws.github.io/eks-charts >/dev/null 2>&1 || true
helm repo update lb-controller

helm upgrade --install aws-load-balancer-controller lb-controller/aws-load-balancer-controller \
  --version "${CHART_VERSION}" \
  -n kube-system \
  -f values.yaml
