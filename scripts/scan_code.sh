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
# --config=auto requires metrics ON (semgrep refuses to run: "Cannot create
# auto config when metrics are off") - confirmed by running it. p/ci is a
# named registry ruleset instead of the project-profiling "auto" config, so
# --metrics=off works with it. Still needs network to fetch the ruleset;
# vendoring it locally is a documented follow-up (see docs/DECISIONS.md).
semgrep --config=p/ci --metrics=off --json --output findings/semgrep.json "${CHANGED_FILES[@]}" 2>/dev/null
SEMGREP_RC=$?

if [ ! -f findings/semgrep.json ]; then
    echo '{"results":[],"errors":[{"type":"ScanFailed","message":"semgrep did not produce output"}]}' > findings/semgrep.json
fi

echo "Semgrep exit code: $SEMGREP_RC (non-zero means findings or error; see findings/semgrep.json)"
exit 0
