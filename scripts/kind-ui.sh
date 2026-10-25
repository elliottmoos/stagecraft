#!/usr/bin/env bash
# Open the Argo Workflows UI of the kind test cluster at https://localhost:2746 (port-forward; stop
# with Ctrl-C). The certificate is self-signed, so the browser warns once. No login: the
# quick-start install runs the server in server auth mode.
set -euo pipefail

context=kind-stagecraft
port="${1:-2746}"

echo "Argo UI: https://localhost:${port} (accept the self-signed certificate; Ctrl-C to stop)"
exec kubectl --context "${context}" -n argo port-forward svc/argo-server "${port}:2746"
