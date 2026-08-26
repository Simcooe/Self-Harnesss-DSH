#!/usr/bin/env python3
"""把 dsh 的 session.jsonl.zstd 导出成结构化购物 trace。

产出（JSON）：
  task            - 任务文本（source.kind == "user" 的那条 user/message）
  steps[]         - 每个交互步：
      step           - step 序号
      tool_name      - 模型调用的工具（search / click / finish）
      tool_args      - 工具参数
      observation    - 模型这一步能看到的文本（observation + 按钮列表）
      meta           - 模型看不到、供评测/诊断用的结构化证据
  terminal        - 终局评测信号（最后一个 tool/result 的 meta 里的 done/终止/奖励）

用法:
  python scripts/export_trace.py <session.jsonl.zstd> [out.json]
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def read_session(path: Path) -> list[dict]:
    raw = subprocess.check_output(
        ["zstd", "-dc", str(path)], stderr=subprocess.DEVNULL
    ).decode("utf-8", "replace")
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def text_of_message(message: dict) -> str:
    for block in message.get("content", []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            return block.get("text", "")
        if "content" in block and isinstance(block["content"], list):
            inner = [b.get("text", "") for b in block["content"] if isinstance(b, dict)]
            if inner:
                return "\n".join(inner)
    return ""


def export(events: list[dict]) -> dict:
    task = ""
    steps = []

    for event in events:
        if event.get("type") != "user/message":
            continue
        source = event.get("data", {}).get("source", {})
        if source.get("kind") == "user":
            task = text_of_message(event.get("data", {}))
            break

    # 逐 step 组装：tool/call 定义动作，紧跟的 tool/result 提供 observation + meta
    pending = None
    for event in events:
        t = event.get("type")
        data = event.get("data", {})

        if t == "tool/call":
            raw_args = data.get("arguments")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = raw_args
            pending = {
                "step": data.get("step"),
                "tool_name": data.get("name"),
                "tool_args": args,
            }
        elif t == "tool/result" and pending is not None:
            message = data.get("message", {})
            meta = data.get("meta")
            steps.append(
                {
                    "step": pending["step"],
                    "tool_name": pending["tool_name"],
                    "tool_args": pending["tool_args"],
                    "observation": text_of_message(message),
                    "meta": meta,
                }
            )
            pending = None

    terminal = None
    for step in reversed(steps):
        meta = step.get("meta")
        if isinstance(meta, dict) and meta.get("done"):
            terminal = {
                "done": meta.get("done"),
                "termination_reason": meta.get("termination_reason"),
                "reward": meta.get("reward"),
                "reward_valid": meta.get("reward_valid"),
                "reward_type": meta.get("reward_type"),
                "purchase_success": meta.get("purchase_success"),
                "purchase": meta.get("purchase"),
            }
            break
    # 环境可能未触发 done 就自然收尾：记录最后一 step 的 meta 作为兜底
    if terminal is None and steps:
        meta = steps[-1].get("meta")
        if isinstance(meta, dict):
            terminal = {
                "done": meta.get("done"),
                "termination_reason": meta.get("termination_reason"),
                "reward": meta.get("reward"),
                "reward_valid": meta.get("reward_valid"),
                "reward_type": meta.get("reward_type"),
                "purchase_success": meta.get("purchase_success"),
                "purchase": meta.get("purchase"),
            }

    return {
        "task": task,
        "step_count": len(steps),
        "steps": steps,
        "terminal": terminal,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1
    src = Path(argv[1])
    dst = Path(argv[2]) if len(argv) > 2 else Path(str(src) + ".trace.json")
    result = export(read_session(src))
    dst.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {dst}  (steps={result['step_count']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
