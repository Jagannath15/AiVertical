#!/bin/bash
# Run Semgrep on files changed in this PR's range.
# Writes findings/semgrep.json; does NOT exit non-zero on findings (semgrep
# exits non-zero when it finds anything, so its exit code is captured
# manually below — never let `set -e` run ahead of that capture).
set -uo pipefail

mkdir -p findings

MERGE_BASE="${MERGE_BASE:-}"
if [ -n "$MERGE_BASE" ]; then
    RANGE="${MERGE_BASE}..HEAD"
else
    RANGE="HEAD^..HEAD"
fi

# --diff-filter=d excludes deleted files (scanning a path that no longer
# exists fails the whole semgrep run). -z + mapfile -d '' handles filenames
# with spaces or a leading '-' safely (no word-splitting, no option injection).
mapfile -d '' CHANGED_FILES < <(
    git diff --name-only --diff-filter=d -z "$RANGE" \
        -- '*.py' '*.js' '*.ts' '*.go' '*.java' '*.rb' '*.c' '*.cpp' '*.cs' '*.php' '*.sh' \
        2>/dev/null
)

if [ ${#CHANGED_FILES[@]} -eq 0 ]; then
    echo '{"results":[],"errors":[]}' > findings/semgrep.json
    echo "No scannable files changed."
    exit 0
fi

echo "Running Semgrep on changed files..."
# --metrics=off: do not send telemetry to semgrep.dev (CLAUDE.md: prefer
# offline tools). --config=auto still fetches rules over the network; vendoring
# a pinned local ruleset is a documented follow-up (see docs/DECISIONS.md).
semgrep --config=auto --metrics=off --json --output findings/semgrep.json "${CHANGED_FILES[@]}" 2>/dev/null
SEMGREP_RC=$?

if [ ! -f findings/semgrep.json ]; then
    echo '{"results":[],"errors":[{"type":"ScanFailed","message":"semgrep did not produce output"}]}' > findings/semgrep.json
fi

echo "Semgrep exit code: $SEMGREP_RC (non-zero means findings or error; see findings/semgrep.json)"
exit 0
