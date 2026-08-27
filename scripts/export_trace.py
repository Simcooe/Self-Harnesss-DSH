#!/usr/bin/env python3
"""把 dsh 的 session.jsonl.zstd 导出成结构化购物 trace。

产出（JSON）分两个视角：
  task            - 任务文本（source.kind == "user" 的那条 user/message）
  model_trace     - 模型可见的 trace（处理过）：
      steps[]       - 每个交互步：
          step / tool_name / tool_args
          observation  - 模型这一步能看到的文本（终局 reward/goal 已裁掉）
      terminal      - 终局评测信号（done/终止/奖励）
  raw_trace       - 环境原生返回（未处理）：
      steps[]       - 每个交互步：tool_name / tool_args / raw
          raw          - 环境 interact 完整 result（含 instruction 原文、goal、reward_detail 等）
      reset         - reset 的原生返回（如果 run_task.sh 落盘了 reset.json）

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


def _raw_state(raw: dict) -> dict:
    """从环境原生 result 里抽出终局评测信号（与模型可见面无关）。"""
    reward_detail = raw.get("reward_detail")
    return {
        "done": raw.get("done"),
        "termination_reason": raw.get("termination_reason"),
        "reward": raw.get("reward"),
        "reward_valid": raw.get("reward_valid"),
        "reward_type": reward_detail.get("reward_type") if isinstance(reward_detail, dict) else None,
        "purchase_success": reward_detail.get("purchase_success") if isinstance(reward_detail, dict) else None,
        "purchase": raw.get("purchase"),
    }


def export(events: list[dict], reset_result: dict | None = None) -> dict:
    task = ""
    for event in events:
        if event.get("type") != "user/message":
            continue
        source = event.get("data", {}).get("source", {})
        if source.get("kind") == "user":
            task = text_of_message(event.get("data", {}))
            break

    model_steps = []
    raw_steps = []

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
            meta = data.get("meta") or {}
            # meta 里存的是 { state, raw }；旧版本可能直接是 state
            raw = meta.get("raw") if isinstance(meta, dict) else None
            state = meta.get("state") if isinstance(meta, dict) else meta

            model_steps.append(
                {
                    "step": pending["step"],
                    "tool_name": pending["tool_name"],
                    "tool_args": pending["tool_args"],
                    "observation": text_of_message(message),
                }
            )
            if raw is not None:
                raw_steps.append(
                    {
                        "step": pending["step"],
                        "tool_name": pending["tool_name"],
                        "tool_args": pending["tool_args"],
                        "raw": raw,
                    }
                )
            pending = None

    # 终局信号：优先取最后一个 done 的 raw；否则兜底取最后一步
    terminal = None
    if raw_steps:
        for step in reversed(raw_steps):
            raw = step.get("raw") or {}
            if raw.get("done"):
                terminal = _raw_state(raw)
                break
        if terminal is None:
            terminal = _raw_state(raw_steps[-1].get("raw") or {})
    elif model_steps:
        terminal = {"done": False, "termination_reason": None, "reward": None, "reward_valid": None, "reward_type": None, "purchase_success": None, "purchase": {}}

    # reset 原生返回：run_task.sh 落盘 reset-env<N>.json，随 session 一起带上
    return {
        "task": task,
        "step_count": len(model_steps),
        "model_trace": {
            "steps": model_steps,
            "terminal": terminal,
        },
        "raw_trace": {
            "reset": reset_result,
            "steps": raw_steps,
        },
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1
    src = Path(argv[1])
    dst = Path(argv[2]) if len(argv) > 2 else Path(str(src) + ".trace.json")

    reset_result = None
    if len(argv) > 3:
        reset_path = Path(argv[3])
        if reset_path.exists():
            payload = json.loads(reset_path.read_text(encoding="utf-8"))
            reset_result = payload.get("result") if isinstance(payload, dict) else None

    result = export(read_session(src), reset_result=reset_result)
    dst.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {dst}  (steps={result['step_count']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
