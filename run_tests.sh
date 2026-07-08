#!/usr/bin/env bash
set -euo pipefail

CONDA_ENV="vphoto"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_DIR="$PROJECT_DIR/tests"
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=== mvimg2livephoto test suite ==="
echo "Project: $PROJECT_DIR"
echo "Env:     $CONDA_ENV"
echo ""

cd "$PROJECT_DIR"

PASSED=0
FAILED=0
FAILED_NAMES=()

run_test_file() {
    local file="$1"
    local name
    name=$(basename "$file" .py)
    printf "%-30s " "$name"
    if [ "${CONDA_DEFAULT_ENV:-}" = "$CONDA_ENV" ]; then
        output=$(python "$file" 2>&1)
    else
        output=$(conda run -n "$CONDA_ENV" python "$file" 2>&1)
    fi
    exit_code=$?
    if [ $exit_code -eq 0 ]; then
        # Extract "N passed" from output
        summary=$(echo "$output" | grep -E "^[0-9]+ passed" | tail -1)
        echo -e "${GREEN}PASS${NC}  $summary"
        PASSED=$((PASSED + 1))
    else
        summary=$(echo "$output" | grep -E "^[0-9]+ passed" | tail -1)
        echo -e "${RED}FAIL${NC}  $summary"
        echo ""
        echo "$output" | grep -E "^\[FAIL\]" | sed 's/^/    /'
        FAILED=$((FAILED + 1))
        FAILED_NAMES+=("$name")
    fi
}

for test_file in \
    "$TEST_DIR/test_parser.py" \
    "$TEST_DIR/test_extractor.py" \
    "$TEST_DIR/test_converter.py" \
    "$TEST_DIR/test_metadata.py" \
    "$TEST_DIR/test_integration.py"; do
    run_test_file "$test_file"
done

echo ""
echo "=================================="
TOTAL=$((PASSED + FAILED))
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All $TOTAL test files passed${NC}"
    exit 0
else
    echo -e "${RED}$FAILED/$TOTAL test files FAILED: ${FAILED_NAMES[*]}${NC}"
    exit 1
fi
