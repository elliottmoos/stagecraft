#!/usr/bin/env bash
# Render every fixture pipeline into a chart and install it into the kind test cluster with the
# test values, to browse and run them in the Argo UI (scripts/kind-ui.sh).
#
# Usage: scripts/kind-demo.sh [--clean]   (--clean first deletes all workflow runs in the namespace)
#
# It shares the integration tests' release (stagecraft-fixture), because Helm won't let two
# releases own the same WorkflowTemplate. A test run replaces these templates; run this again
# afterwards.
set -euo pipefail

context=kind-stagecraft
namespace=argo
release=stagecraft-fixture
root="$(cd "$(dirname "$0")/.." && pwd)"
chart="$(mktemp -d)/chart"
trap 'rm -rf "$(dirname "${chart}")"' EXIT

if [[ "${1:-}" == "--clean" ]]; then
  kubectl --context "${context}" -n "${namespace}" delete workflows --all
fi

# Fixture pipelines import their actions relative to tests/, as pipeline files do in a repo.
(cd "${root}/tests" && uv run stagecraft render fixtures/pipelines --out "${chart}" \
  --chart-name "${release}" > /dev/null)
helm upgrade --install "${release}" "${chart}" -f "${root}/tests/fixtures/values.yaml" \
  --kube-context "${context}" -n "${namespace}" > /dev/null

echo "Installed into ${context}, namespace ${namespace}:"
kubectl --context "${context}" -n "${namespace}" get workflowtemplates,cronworkflows
echo
echo "Browse them with scripts/kind-ui.sh, or submit one from the CLI, for example:"
echo "  argo --context ${context} -n ${namespace} submit --from workflowtemplate/smoke-ci -p repo=example/repo --watch"
