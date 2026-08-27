#!/usr/bin/env bash
# 跑一次购物任务：租 ShopSimulator slot → 注入 env_idx → 跑 dsh → 释放 slot。
#
# 用法:
#   scripts/run_task.sh "<购物任务指令>"
#
# 依赖环境变量（从 .env 读取）:
#   SHOPSIM_BASE_URL    ShopSimulator 地址（默认 http://127.0.0.1:5700）
#   DSH_HOME            dsh profile home
#   DEEPSEEK_API_KEY    模型密钥（dsh 内部读取）
#
# 可选:
#   SHOPSIM_TASK_IDX    固定任务 id（默认 0）；并行时由上层传入不同 id

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASK="${1:?用法: run_task.sh \"<任务指令>\"}"

# 从 .env 读配置（key 不进命令行）
if [[ -f "$ROOT/.env" ]]; then
  set -a
  source "$ROOT/.env"
  set +a
fi

SHOPSIM_BASE_URL="${SHOPSIM_BASE_URL:-http://127.0.0.1:5700}"
DSH_HOME="${DSH_HOME:-$ROOT/.dsh-home}"
TASK_IDX="${SHOPSIM_TASK_IDX:-0}"

# 租 slot：reset 返回 env_idx
RESET_JSON="$(curl -s -X POST "$SHOPSIM_BASE_URL/api/shop_agent" \
  -H 'Content-Type: application/json' \
  -d "{\"action\":\"reset\",\"idx\":$TASK_IDX}")"
ENV_IDX="$(echo "$RESET_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["env_idx"])')"

# 落盘 reset 原生返回（供 raw_trace 使用）
RUNS_DIR="${TRACE_OUT_DIR:-$ROOT/runs}"
mkdir -p "$RUNS_DIR"
echo "$RESET_JSON" > "$RUNS_DIR/reset-env$ENV_IDX.json"

echo "==> leased env_idx=$ENV_IDX (task_idx=$TASK_IDX)" >&2

cleanup() {
  curl -s -X POST "$SHOPSIM_BASE_URL/api/shop_agent" \
    -H 'Content-Type: application/json' \
    -d "{\"action\":\"release_one\",\"env_idx\":$ENV_IDX}" >/dev/null 2>&1 || true
  echo "==> released env_idx=$ENV_IDX" >&2
}
trap cleanup EXIT

export SHOPSIM_ENV_IDX="$ENV_IDX"
export SHOPSIM_BASE_URL
export DSH_HOME

# dsh 跑在 deps/deepseek-harness checkout 里；从那里用 pnpm 启动
DSH_CHECKOUT="${DSH_CHECKOUT:-$ROOT/deps/deepseek-harness}"
cd "$DSH_CHECKOUT"
pnpm dsh --profile headless "$TASK"
