#!/usr/bin/env bash
# Create (or reuse) the kind cluster for integration tests and install Argo Workflows into it.
# The Argo version matches the committed CRD schemas. Tear down with: kind delete cluster --name stagecraft
set -euo pipefail

cluster=stagecraft
context="kind-${cluster}"
argo_version="$(cat "$(dirname "$0")/../tests/fixtures/crd-schemas/ARGO_VERSION")"

if ! kind get clusters | grep -qx "${cluster}"; then
  kind create cluster --name "${cluster}" --wait 120s
fi

kubectl --context "${context}" create namespace argo --dry-run=client -o yaml \
  | kubectl --context "${context}" apply -f -
# Server-side apply: the full CRDs exceed the client-side last-applied annotation limit.
kubectl --context "${context}" apply -n argo --server-side --force-conflicts \
  -f "https://github.com/argoproj/argo-workflows/releases/download/v${argo_version}/quick-start-minimal.yaml"
kubectl --context "${context}" -n argo rollout status deployment/workflow-controller --timeout=300s

echo "Argo Workflows v${argo_version} is running in kind cluster '${cluster}' (context ${context})."
