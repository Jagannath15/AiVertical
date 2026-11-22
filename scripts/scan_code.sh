#!/bin/bash
# Run Semgrep on files changed in this PR's range.
# Writes findings/semgrep.json; does NOT exit non-zero on findings.
set -uo pipefail

mkdir -p findings

MERGE_BASE="${MERGE_BASE:-}"

if [ -n "$MERGE_BASE" ]; then
    RANGE="${MERGE_BASE}..HEAD"
else
    RANGE="HEAD^..HEAD"
fi

# Collect changed files matching supported extensions
CHANGED=$(git diff --name-only "$RANGE" 2>/dev/null | grep -E '\.(py|js|ts|go|java|rb|c|cpp|cs|php|sh)$' || true)

if [ -z "$CHANGED" ]; then
    echo '{"results":[],"errors":[]}' > findings/semgrep.json
    echo "No scannable files changed."
    exit 0
fi

echo "Running Semgrep on changed files..."
# semgrep exits non-zero when findings exist — capture exit code manually
# shellcheck disable=SC2086
semgrep --config=auto --json --output findings/semgrep.json $CHANGED 2>/dev/null
SEMGREP_RC=$?

# Ensure the file exists even if semgrep failed to write it
if [ ! -f findings/semgrep.json ]; then
    echo '{"results":[],"errors":[{"type":"SemgrepError","message":"semgrep did not produce output"}]}' > findings/semgrep.json
fi

echo "Semgrep exit code: $SEMGREP_RC (non-zero means findings or error; see findings/semgrep.json)"
exit 0
