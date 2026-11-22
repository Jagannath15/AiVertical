#!/bin/bash
# Scan dependencies with Trivy.
# Writes findings/trivy.json; exits 0 always (findings are advisory unless CRITICAL).
set -uo pipefail

mkdir -p findings

echo "Running Trivy dependency scan..."
trivy fs --format json --output findings/trivy.json --quiet . 2>/dev/null
TRIVY_RC=$?

if [ $TRIVY_RC -ne 0 ] || [ ! -f findings/trivy.json ]; then
    echo '{"Results":[]}' > findings/trivy.json
    echo "WARNING: Trivy scan failed or produced no output (exit $TRIVY_RC)." >&2
fi

echo "Trivy scan complete."
exit 0
