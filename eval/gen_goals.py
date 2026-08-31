#!/usr/bin/env python3
"""批量读数据集 → get_goals → 投影 goal（剔除答案泄漏字段）→ 落盘 JSONL 中间文件。

用 ShopSimulator 的 venv python 运行（需加载 engine/goal 模块）：
  environments/ShopSimulator/.venv-shopsim/bin/python3.10 eval/gen_goals.py \
      --data environments/ShopSimulator/shop_env/data/items_eval_train.json \
      --out eval/goals.jsonl --limit 6
"""
import argparse
import json
import sys
from pathlib import Path

# 把 shop_env 挂到 sys.path，保证无论从哪个 cwd 调用都能 import web_agent_site
SHOP_ENV = Path(__file__).resolve().parents[1] / "environments" / "ShopSimulator" / "shop_env"
sys.path.insert(0, str(SHOP_ENV))

from web_agent_site.engine.engine import load_products  # noqa: E402
from web_agent_site.engine.goal import get_goals  # noqa: E402

# goal 投影保留字段：只用于「提炼规则 + 判断陷阱」，不泄漏答案
GOAL_KEEP_FIELDS = [
    "category",
    "attributes",
    "expected_core_functions",
    "expected_brand",
    "expected_model",
    "goal_options",
    "required_options_by_key",
    "price_upper",
    "user_persona",
]


def project_goal(goal):
    return {k: goal.get(k) for k in GOAL_KEEP_FIELDS}


def main():
    ap = argparse.ArgumentParser(description="投影 goal 并落盘 JSONL")
    ap.add_argument("--data", required=True, help="items_eval_train.json 路径")
    ap.add_argument("--out", required=True, help="输出 JSONL 路径")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 个任务")
    args = ap.parse_args()

    # limit 时只加载前 N 个商品（每个商品对应 1 条任务），避免全量加载的几十秒开销
    num_products = args.limit if args.limit else None
    all_products, _item_dict, product_prices, _attr_to_asins = load_products(
        args.data, num_products=num_products, human_goals=True
    )
    goals = get_goals(all_products, product_prices, if_persona=False)
    if args.limit is not None:
        goals = goals[: args.limit]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    # task_id 必须用 goal 在 get_goals 返回列表里的位置序号（goal_idx）。
    # 运行侧 run_batch.sh / run_task.sh 用同一个序号做 reset 的 idx，trace 文件也按这个
    # 序号命名（traces/<idx>.raw_trace.json），所以要用位置序号对齐，不能用 asin。
    # asin 仅作溯源调试字段保留，不进 Flash 输入、不进 Judge。
    with open(out, "w", encoding="utf-8") as f:
        for idx, goal in enumerate(goals):
            task = {
                "task_id": str(idx),
                "asin": goal["asin"],
                "query": goal["instruction_text"],
                "goal": project_goal(goal),
            }
            f.write(json.dumps(task, ensure_ascii=False) + "\n")

    print(f"wrote {len(goals)} goals -> {out}")


if __name__ == "__main__":
    main()
