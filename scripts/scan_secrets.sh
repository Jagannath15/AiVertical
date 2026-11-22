#!/bin/bash
# Scan committed range for secrets with Gitleaks.
# Writes findings/secrets.json. Exits 1 if secrets found, OR if the scan
# itself could not be verified (missing binary, crash, bad range) — fail CLOSED,
# never silently report clean when the scanner didn't actually run.
set -uo pipefail

mkdir -p findings
REPORT="findings/.gitleaks_raw.json"
rm -f "$REPORT"

MERGE_BASE="${MERGE_BASE:-}"
if [ -n "$MERGE_BASE" ]; then
    LOG_OPTS="${MERGE_BASE}..HEAD"
else
    LOG_OPTS="HEAD^..HEAD"
fi

gitleaks detect --source . \
    --log-opts "$LOG_OPTS" \
    --report-format json --report-path "$REPORT" \
    --redact \
    --no-banner
RC=$?

if [ $RC -eq 0 ]; then
    rm -f "$REPORT"
    echo '{"found":false}' > findings/secrets.json
    echo "No secrets found."
    exit 0
fi

if [ $RC -eq 1 ] && [ -s "$REPORT" ]; then
    COUNT=$(python3 -c "import json; print(len(json.load(open('$REPORT'))))")
    RULES=$(python3 -c "
import json
d = json.load(open('$REPORT'))
rules = sorted(set(x.get('RuleID','') for x in d if x.get('RuleID')))
print(json.dumps(rules))
")
    printf '{"found":true,"count":%s,"rules":%s}\n' "$COUNT" "$RULES" > findings/secrets.json
    rm -f "$REPORT"
    echo "SECRETS FOUND: $COUNT secret(s) detected. Merge blocked." >&2
    exit 1
fi

# Any other outcome (gitleaks missing/crashed/bad range) is unverifiable. Fail closed.
rm -f "$REPORT"
echo "{\"found\":null,\"error\":\"gitleaks scan could not be verified (exit $RC)\"}" > findings/secrets.json
echo "ERROR: could not verify secret scan (gitleaks exit $RC). Failing closed." >&2
exit 1
