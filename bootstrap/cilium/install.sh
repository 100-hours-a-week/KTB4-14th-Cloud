#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

CILIUM_VERSION="1.20.4"

helm repo add cilium https://helm.cilium.io >/dev/null 2>&1 || true
helm repo update cilium

helm upgrade --install cilium cilium/cilium \
  --version "${CILIUM_VERSION}" \
  -n kube-system \
  -f values.yaml
