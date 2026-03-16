#!/usr/bin/env bash
# 运行所有测试（单元 + 集成）
# 用法：
#   cd /home/admin/.openclaw/workspace/test
#   bash run_tests.sh              # 运行全部
#   bash run_tests.sh unit         # 仅单元测试
#   bash run_tests.sh integration  # 仅集成测试

set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "$0")/../skills/recruit-ops/scripts" && pwd)"
export PYTHONPATH="$SCRIPTS_DIR:${PYTHONPATH:-}"

# 临时状态文件（测试用，各测试用例自己创建 tempfile，此变量仅作保底回退）
export RECRUIT_STATE_PATH="/tmp/recruit_state_test_$$.json"
echo '{"candidates":{}}' > "$RECRUIT_STATE_PATH"
trap 'rm -f "$RECRUIT_STATE_PATH"' EXIT

MODE="${1:-all}"
TEST_DIR="$(cd "$(dirname "$0")" && pwd)"

run_unit() {
    echo "=== 单元测试 ==="
    python3 -m pytest "$TEST_DIR/unit/" -v --tb=short
}

run_integration() {
    echo "=== 集成测试 ==="
    python3 -m pytest "$TEST_DIR/integration/" -v --tb=short
}

case "$MODE" in
    unit)
        run_unit
        ;;
    integration)
        run_integration
        ;;
    all)
        run_unit
        echo ""
        run_integration
        ;;
    *)
        echo "用法: bash run_tests.sh [all|unit|integration]"
        exit 1
        ;;
esac
