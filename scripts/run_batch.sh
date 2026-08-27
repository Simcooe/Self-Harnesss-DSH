#!/usr/bin/env bash
# 跑一批独立购物任务，落盘到 runs/<时间戳>/。
#
# 用法:
#   scripts/run_batch.sh <goal_count>
#     跑 goal_idx = 0 .. goal_count-1 的 goal（每个 goal 一个独立 dsh session）
#
# 落盘结构:
#   runs/<MMDD-HHMM>/
#     sessions/<session-uuid>/session.jsonl.zstd   ← dsh 原始 session
#     traces/<goal_idx>.model_trace.json
#     traces/<goal_idx>.raw_trace.json
#     reset/<goal_idx>.json
#     manifest.json                                 ← goal_idx -> session uuid 映射
#
# 依赖: scripts/setup.sh 已跑，ShopSimulator 服务已起（scripts/start_environment.sh）

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GOAL_COUNT="${1:?用法: run_batch.sh <goal_count>}"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  source "$ROOT/.env"
  set +a
fi

SHOPSIM_BASE_URL="${SHOPSIM_BASE_URL:-http://127.0.0.1:5700}"
DSH_HOME="${DSH_HOME:-$ROOT/.dsh-home}"
DSH_CHECKOUT="${DSH_CHECKOUT:-$ROOT/deps/deepseek-harness}"
SESSIONS_ROOT="$DSH_HOME/sessions"

RUN_TS="$(date +%m%d-%H%M)"
RUN_DIR="$ROOT/runs/$RUN_TS"
mkdir -p "$RUN_DIR/sessions" "$RUN_DIR/traces" "$RUN_DIR/reset"

declare -a MANIFEST

for idx in $(seq 0 $((GOAL_COUNT - 1))); do
  echo "==> goal_idx=$idx"

  # 租 slot，取任务指令（去前缀 "Instruction: "）
  RESET_JSON="$(curl -s -X POST "$SHOPSIM_BASE_URL/api/shop_agent" \
    -H 'Content-Type: application/json' \
    -d "{\"action\":\"reset\",\"idx\":$idx}")"
  ENV_IDX="$(echo "$RESET_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["env_idx"])')"
  TASK_TEXT="$(echo "$RESET_JSON" | python3 -c '
import sys, json
instr = json.load(sys.stdin)["result"]["instruction"]
print(instr[len("Instruction: "):] if instr.startswith("Instruction: ") else instr)
')"
  echo "$RESET_JSON" > "$RUN_DIR/reset/$idx.json"

  # 记下 run 前已有的 session 目录集合
  BEFORE=$(mktemp)
  find "$SESSIONS_ROOT" -name 'session-*' -type d 2>/dev/null | sort > "$BEFORE" || true

  # 跑一个 goal
  SHOPSIM_ENV_IDX="$ENV_IDX" \
  SHOPSIM_BASE_URL="$SHOPSIM_BASE_URL" \
  DSH_HOME="$DSH_HOME" \
    bash -c "cd '$DSH_CHECKOUT' && pnpm dsh --profile headless \"\$1\"" _ "$TASK_TEXT" > /dev/null 2>&1 || true

  # 找出这个 goal 产生的新 session
  AFTER=$(mktemp)
  find "$SESSIONS_ROOT" -name 'session-*' -type d 2>/dev/null | sort > "$AFTER" || true
  NEW_SESSION=$(comm -13 "$BEFORE" "$AFTER" | head -1)

  # 释放 slot
  curl -s -X POST "$SHOPSIM_BASE_URL/api/shop_agent" \
    -H 'Content-Type: application/json' \
    -d "{\"action\":\"release_one\",\"env_idx\":$ENV_IDX}" >/dev/null 2>&1 || true

  if [[ -n "$NEW_SESSION" ]]; then
    SESSION_UUID="$(basename "$NEW_SESSION")"
    # 拷贝 session 到 run 目录
    cp -R "$NEW_SESSION" "$RUN_DIR/sessions/$SESSION_UUID"
    SESSION_FILE="$RUN_DIR/sessions/$SESSION_UUID/session.jsonl.zstd"
    python3 "$ROOT/scripts/export_trace.py" "$SESSION_FILE" \
      --out-dir "$RUN_DIR/traces" --id "$idx" --reset "$RUN_DIR/reset/$idx.json"
    MANIFEST+=("{\"goal_idx\":$idx,\"session\":\"$SESSION_UUID\",\"env_idx\":$ENV_IDX}")
    echo "    session=$SESSION_UUID"
  else
    echo "    WARN: goal_idx=$idx 未产生新 session" >&2
  fi

  rm -f "$BEFORE" "$AFTER"
done

printf '%s\n' "${MANIFEST[@]}" | python3 -c "
import sys, json
rows = [json.loads(l) for l in sys.stdin if l.strip()]
json.dump({'run_ts': '$RUN_TS', 'goal_count': $GOAL_COUNT, 'goals': rows}, open('$RUN_DIR/manifest.json','w'), ensure_ascii=False, indent=2)
"
echo "==> 完成。落盘目录: $RUN_DIR"
