#!/bin/sh
# Workflow Vault — run the unit suite, then the demo end-to-end flow.
# Exits non-zero on any failure.
set -e
cd "$(dirname "$0")"

echo "=== unit tests ==="
python3 -m unittest discover -s tests -v

echo ""
echo "=== demo e2e ==="
DEMO="$(mktemp -d)"
trap 'rm -rf "$DEMO"' EXIT INT TERM
echo "demo vault: $DEMO"

python3 -m workflow init --dir "$DEMO"
python3 -m workflow add --vault "$DEMO" --file "$DEMO/sample-workflow.json"
python3 -m workflow add --vault "$DEMO" --file "$DEMO/second-workflow.json"
python3 -m workflow list --vault "$DEMO" > /dev/null
python3 -m workflow test --vault "$DEMO" --name summarize-file
python3 -m workflow test --vault "$DEMO" --name word-count

# run summarize-file and check the real output against the hand-computed
# values in examples/expected-run.json
python3 -m workflow run --vault "$DEMO" --name summarize-file \
  --input file=examples/sample.txt > "$DEMO/run-output.txt"
grep -q "step count: exit_code=0 PASS" "$DEMO/run-output.txt"
grep -q "step head: exit_code=0 PASS" "$DEMO/run-output.txt"
grep -q "overall: PASS" "$DEMO/run-output.txt"

RUN_LOG="$(ls "$DEMO"/runs/summarize-file-1.0.0-*.json | head -1)"
python3 - "$RUN_LOG" <<'PY'
import json, sys
log = json.load(open(sys.argv[1]))
expected = json.load(open("examples/expected-run.json"))["expected_run"]
assert log["passed"] is True, log
assert log["name"] == "summarize-file" and log["version"] == "1.0.0"
for got, want in zip(log["steps"], expected["steps"]):
    assert got["id"] == want["id"], (got, want)
    assert got["command"] == want["command"], (got, want)
    assert got["exit_code"] == want["exit_code"], (got, want)
    assert got["stdout"] == want["stdout"], (got, want)
    assert got["passed"] is want["passed"], (got, want)
print("demo run log matches hand-computed expected-run.json:")
print("  count -> %r (exit 0, PASS)" % log["steps"][0]["stdout"])
print("  head  -> %r (exit 0, PASS)" % log["steps"][1]["stdout"])
PY

python3 -m workflow verify --vault "$DEMO" --name summarize-file
python3 -m workflow verify --vault "$DEMO" --name word-count
echo "demo done."
