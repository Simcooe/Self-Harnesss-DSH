#!/usr/bin/env bash
# 并行跑 N 个购物任务，每个任务租一个 ShopSimulator slot。
#
# 用法:
#   scripts/run_parallel.sh "<任务指令1>" "<任务指令2>" "<任务指令3>" ...
#
# 依赖: scripts/run_task.sh（每个任务内部自己 reset/release slot）

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $# -eq 0 ]]; then
  echo "用法: run_parallel.sh \"任务1\" \"任务2\" ..." >&2
  exit 1
fi

PIDS=()
i=0
for TASK in "$@"; do
  i=$((i + 1))
  # 每个任务用不同 task_idx（0-based），并行时互不抢占
  SHOPSIM_TASK_IDX=$((i - 1)) "$ROOT/scripts/run_task.sh" "$TASK" \
    > "$ROOT/.parallel-task-$i.log" 2>&1 &
  PIDS+=($!)
done

echo "==> 已启动 ${#PIDS[@]} 个并行任务" >&2

FAIL=0
for pid in "${PIDS[@]}"; do
  if ! wait "$pid"; then
    FAIL=1
  fi
done

echo "==> 所有任务结束" >&2

# 汇总每个任务的最后几行
for idx in $(seq 1 ${#PIDS[@]}); do
  echo "======== 任务 $idx 结尾 ========"
  tail -n 8 "$ROOT/.parallel-task-$idx.log" 2>/dev/null || true
done

exit "$FAIL"
