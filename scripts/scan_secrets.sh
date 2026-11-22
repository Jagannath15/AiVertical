#!/bin/bash
# Scan committed range for secrets with Gitleaks.
# Writes findings/secrets.json; exits 1 if secrets found.
set -uo pipefail

mkdir -p findings

MERGE_BASE="${MERGE_BASE:-}"
REPORT=/tmp/gitleaks_report.json

if [ -n "$MERGE_BASE" ]; then
    LOG_OPTS="${MERGE_BASE}..HEAD"
else
    LOG_OPTS="HEAD^..HEAD"
fi

# Allow gitleaks non-zero exit (findings); capture it ourselves
gitleaks detect --source . \
    --log-opts "$LOG_OPTS" \
    --report-format json --report-path "$REPORT" \
    --no-banner 2>/dev/null || true

if [ -s "$REPORT" ] && [ "$(cat "$REPORT")" != "[]" ]; then
    COUNT=$(python3 -c "import json,sys; d=json.load(open('$REPORT')); print(len(d))")
    RULES=$(python3 -c "
import json
d = json.load(open('$REPORT'))
rules = list(set(x.get('RuleID','') for x in d if x.get('RuleID')))
import json as j; print(j.dumps(rules))
")
    printf '{"found":true,"count":%s,"rules":%s}\n' "$COUNT" "$RULES" > findings/secrets.json
    echo "SECRETS FOUND: $COUNT secret(s) detected. Merge blocked." >&2
    exit 1
fi

echo '{"found":false}' > findings/secrets.json
echo "No secrets found."
