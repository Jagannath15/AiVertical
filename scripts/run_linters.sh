#!/bin/bash
# Auto-detect project language and run its linter/tests.
# Writes findings/linters.json with {passed, tool, output}.
set -uo pipefail

mkdir -p findings

write_result() {
    local passed="$1" tool="$2" output="$3"
    # Truncate to 2000 chars and JSON-escape via python3
    python3 -c "
import json, sys
output = sys.argv[1][:2000]
json.dump({'passed': $passed, 'tool': '$tool', 'output': output}, open('findings/linters.json', 'w'))
" "$output"
}

if [ -f pyproject.toml ] || [ -f setup.py ] || [ -f requirements.txt ]; then
    TOOL="flake8"
    OUTPUT=$(flake8 . --max-line-length=120 --statistics 2>&1)
    RC=$?
elif [ -f package.json ]; then
    TOOL="npm"
    OUTPUT=$( (npm run lint --if-present && npm test) 2>&1 )
    RC=$?
elif [ -f go.mod ]; then
    TOOL="go"
    OUTPUT=$( (go vet ./... && go test ./...) 2>&1 )
    RC=$?
elif [ -f pom.xml ]; then
    TOOL="maven"
    OUTPUT=$(mvn test -q 2>&1)
    RC=$?
else
    echo '{"passed":true,"tool":"none","output":"No recognized language"}' > findings/linters.json
    echo "No recognized language — skipping linters."
    exit 0
fi

if [ $RC -eq 0 ]; then
    write_result "True" "$TOOL" "$OUTPUT"
    echo "$TOOL passed."
else
    write_result "False" "$TOOL" "$OUTPUT"
    echo "$TOOL FAILED." >&2
fi
exit 0
