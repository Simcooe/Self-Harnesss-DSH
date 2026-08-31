#!/usr/bin/env python3
"""四面板评测报告：并排呈现 evaluate.py（面板①④）与 judge.py（面板②③），不合成总分。

纯标准库，用系统 python3 运行：
  python3 eval/report.py --traces runs/0827-1617/traces --judgments eval/judgments --rubrics eval/rubrics
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import evaluate

# judge 的五维度（面板③），与 raw 里的 reward 维度（面板①）完全无关，不要混用
JUDGE_DIMENSION_KEYS = [
    "search_strategy",
    "candidate_utilization",
    "evidence_verification",
    "decision_quality",
    "termination_efficiency",
]
REWARD_DIMENSION_KEYS = ["brand", "core_functions", "key_options", "model"]


def _reward_dim_tag(score, active):
    if active is True and score == 0:
        return "真失败"
    if active is False and score == 0:
        return "不考核"
    return "正常"


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_trace_ids(traces_dir):
    traces_dir = Path(traces_dir)
    if not traces_dir.is_dir():
        return []
    ids = []
    for f in traces_dir.glob("*.raw_trace.json"):
        stem = f.name.split(".raw_trace.json")[0]
        if stem.isdigit():
            ids.append(stem)
    return sorted(ids, key=int)


def build_panel1(trace):
    """面板①：环境 reward 与终局结果。字段全部来自 raw_trace 终局步。"""
    final = evaluate.final_raw(trace)
    rd = final.get("reward_detail") or {}
    reward = (
        rd.get("reward")
        if isinstance(rd, dict) and rd.get("reward") is not None
        else final.get("reward")
    )
    reward_type = rd.get("reward_type")
    purchase_success = rd.get("purchase_success")
    reward_valid = final.get("reward_valid") if final.get("done") else None
    term = final.get("termination_reason")
    over = bool(final.get("over"))
    hard_gates = rd.get("hard_gates") or {}
    dim_scores = rd.get("dimension_scores") or {}
    dims_active = {}
    pref = (rd.get("evidence") or {}).get("preference_scoring") or {}
    for name, d in (pref.get("dimensions") or {}).items():
        dims_active[name] = bool(d.get("active"))
    return {
        "reward": reward,
        "reward_type": reward_type,
        "purchase_success": bool(purchase_success),
        "reward_valid": reward_valid,
        "termination_reason": term,
        "over": over,
        "hard_gates": hard_gates,
        "dim_scores": dim_scores,
        "dims_active": dims_active,
        "goal": final.get("goal") or {},
        "purchase": final.get("purchase") or {},
    }


def build_panel4(trace, p1):
    """面板④：确定性行为指标 + 失败归类 + 撞对，复用 evaluate 的 collect/classify 逻辑。"""
    ev, product_asins, max_cr, max_np, finish = evaluate.collect_process(trace)
    goal = p1["goal"]
    gold_asin = goal.get("asin")
    gold_seen = evaluate.gold_seen_in_observations(trace, gold_asin)
    n_result_sets_distinct = len(set(ev.get("result_set", [])))
    n_products = len(product_asins)
    n_options = len(ev.get("option", []))
    n_subpages = len(ev.get("subpage", []))
    collision, collision_reason = evaluate.compute_collision(
        p1["purchase_success"], goal, p1["hard_gates"], ev, product_asins
    )
    cls = evaluate.classify_failure(
        p1["termination_reason"], ev, product_asins, p1["reward_type"],
        p1["purchase_success"], p1["hard_gates"], gold_asin, gold_seen,
        max_cr, max_np, finish, p1["over"], n_result_sets_distinct,
        purchase_asin=(p1["purchase"] or {}).get("asin"),
    )
    return {
        "step_count": trace.get("step_count"),
        "search": n_result_sets_distinct,
        "products": n_products,
        "options": n_options,
        "subpages": n_subpages,
        "max_consec_repeats": max_cr,
        "finish": finish,
        "over": p1["over"],
        "class": cls[0] if cls else "成功",
        "class_basis": cls[1] if cls else "",
        "collision": collision,
        "collision_reason": collision_reason,
    }


def build_panel23(rubric, judgment):
    """面板②③：judge 的 verdict 与 rubric 按 candidate_id join，以及五维度分。"""
    if not judgment:
        return [], None
    constraints = (rubric or {}).get("rubric", {}).get("selected_constraints") or []
    verdicts = (
        (judgment.get("judgment") or {}).get("rubric_verdicts") or []
    )
    verdict_by_id = {v.get("candidate_id"): v for v in verdicts}
    rows = []
    for c in constraints:
        cid = c.get("candidate_id")
        v = verdict_by_id.get(cid)
        rows.append(
            {
                "candidate_id": cid,
                "description": c.get("description"),
                "hardness": c.get("hardness"),
                "status": v.get("status") if v else None,
                "step_reference": v.get("step_reference") if v else None,
                "reasoning": v.get("reasoning") if v else None,
            }
        )
    dims = (judgment.get("judgment") or {}).get("dimension_scores") or {}
    return rows, dims


def format_panel1(p1):
    lines = []
    rv = p1["reward_valid"]
    rv_str = "true" if rv is True else ("false" if rv is False else "无")
    lines.append(
        f"    reward: {p1['reward']} | reward_type: {p1['reward_type']} | "
        f"purchase_success: {'true' if p1['purchase_success'] else 'false'} | reward_valid: {rv_str}"
    )
    if p1["hard_gates"]:
        gates = ", ".join(
            f"{k}={'pass' if v.get('passed') else 'fail'}"
            for k, v in p1["hard_gates"].items()
        )
    else:
        gates = "(无)"
    lines.append(f"    hard_gates: {gates}")
    if p1["dim_scores"]:
        parts = []
        for name in REWARD_DIMENSION_KEYS:
            if name not in p1["dim_scores"]:
                continue
            score = p1["dim_scores"][name]
            tag = _reward_dim_tag(score, p1["dims_active"].get(name))
            parts.append(f"{name}={score}({tag})")
        lines.append(f"    reward 维度分: {' '.join(parts)}")
    else:
        lines.append("    reward 维度分: (无)")
    return lines


def format_panel2(rows, has_judgment):
    if not has_judgment:
        return ["    无 Judge 结果（可能被跳过/失败）"]
    lines = []
    for r in rows:
        status = r["status"] or "?"
        ref = r["step_reference"] if r["step_reference"] else []
        ref_str = f"ref={ref}" if ref else "ref=[]"
        reason = r["reasoning"] or ""
        lines.append(
            f"    {r['candidate_id']} [{r['hardness']}] {r['description']:<24s} → {status:<10s} {ref_str}  {reason}"
        )
    return lines


def format_panel3(dims, has_judgment):
    if not has_judgment:
        return ["    无 Judge 结果（可能被跳过/失败）"]
    return ["    " + " ".join(f"{k}={dims.get(k, '?')}" for k in JUDGE_DIMENSION_KEYS)]


def main():
    ap = argparse.ArgumentParser(description="四面板评测报告")
    ap.add_argument("--traces", required=True, help="trace 目录")
    ap.add_argument("--judgments", required=True, help="judgment 目录")
    ap.add_argument("--rubrics", required=True, help="rubric 目录")
    ap.add_argument("--out", default=None, help="可选，输出结构化 JSON")
    args = ap.parse_args()

    traces_dir = Path(args.traces)
    judgments_dir = Path(args.judgments)
    rubrics_dir = Path(args.rubrics)

    ids = find_trace_ids(args.traces)
    if not ids:
        print("未找到 raw_trace.json", file=sys.stderr)
        sys.exit(1)

    tasks_out = {}
    summary_rows = []

    for tid in ids:
        trace = load_json(traces_dir / f"{tid}.raw_trace.json")
        rubric_path = rubrics_dir / f"{tid}.json"
        judgment_path = judgments_dir / f"{tid}.json"
        rubric = load_json(rubric_path) if rubric_path.exists() else None
        judgment = load_json(judgment_path) if judgment_path.exists() else None

        query = (
            (rubric or {}).get("query")
            or (judgment or {}).get("query")
            or trace.get("task")
            or ""
        )

        p1 = build_panel1(trace)
        p4 = build_panel4(trace, p1)
        rows, dims = build_panel23(rubric, judgment)
        has_judgment = judgment is not None

        print(f"========== task {tid} ==========")
        print(f"Query: {query}")
        print("[面板① 环境 Reward 和终局结果]")
        for line in format_panel1(p1):
            print(line)
        print("[面板② 用户需求满足情况]")
        for line in format_panel2(rows, has_judgment):
            print(line)
        print("[面板③ 轨迹过程质量]")
        for line in format_panel3(dims, has_judgment):
            print(line)
        print("[面板④ 确定性行为指标]")
        print(
            f"    步数: {p4['step_count']} | 搜索: {p4['search']} | 打开商品: {p4['products']} | "
            f"选规格: {p4['options']} | 子页: {p4['subpages']} | 最大连续重复: {p4['max_consec_repeats']} | "
            f"finish: {'是' if p4['finish'] else '否'} | over: {'true' if p4['over'] else 'false'}"
        )
        collision_note = f" | 撞对标记: {'疑似撞对' if p4['collision'] else '无'}"
        print(f"    失败归类: {p4['class']}{collision_note}")
        print()

        tasks_out[tid] = {
            "query": query,
            "panel1": {
                "reward": p1["reward"],
                "reward_type": p1["reward_type"],
                "purchase_success": p1["purchase_success"],
                "reward_valid": p1["reward_valid"],
                "termination_reason": p1["termination_reason"],
                "hard_gates": {k: bool(v.get("passed")) for k, v in p1["hard_gates"].items()},
                "reward_dimensions": {
                    name: {
                        "score": p1["dim_scores"].get(name),
                        "active": p1["dims_active"].get(name),
                        "tag": _reward_dim_tag(
                            p1["dim_scores"].get(name, 0), p1["dims_active"].get(name)
                        ),
                    }
                    for name in REWARD_DIMENSION_KEYS
                    if name in p1["dim_scores"]
                },
            },
            "panel2": rows,
            "panel3": dims,
            "panel4": {
                "step_count": p4["step_count"],
                "search": p4["search"],
                "products": p4["products"],
                "options": p4["options"],
                "subpages": p4["subpages"],
                "max_consec_repeats": p4["max_consec_repeats"],
                "finish": p4["finish"],
                "over": p4["over"],
                "class": p4["class"],
                "collision": p4["collision"],
            },
        }
        summary_rows.append({"tid": tid, "p1": p1, "p4": p4, "dims": dims, "has_judgment": has_judgment})

    # 汇总
    total = len(summary_rows)
    success = [
        r for r in summary_rows
        if r["p1"]["purchase_success"] or r["p1"]["reward_type"] == "gold_purchase"
    ]
    rewards = [
        r["p1"]["reward"] if isinstance(r["p1"]["reward"], (int, float)) else 0.0
        for r in summary_rows
    ]
    reward_mean = sum(rewards) / total if total else 0.0
    term_dist = Counter((r["p1"]["termination_reason"] or "(none)") for r in summary_rows)
    collisions = [r for r in summary_rows if r["p4"]["collision"]]

    with_judge = [r for r in summary_rows if r["has_judgment"]]
    avg_dims = {}
    for k in JUDGE_DIMENSION_KEYS:
        vals = [r["dims"].get(k) for r in with_judge if isinstance(r["dims"].get(k), (int, float))]
        avg_dims[k] = round(sum(vals) / len(vals), 1) if vals else None

    n_satisfied = 0
    n_verdicts = 0
    for tid in ids:
        rows = tasks_out[tid]["panel2"]
        for r in rows:
            if r["status"] is not None:
                n_verdicts += 1
                if r["status"] == "satisfied":
                    n_satisfied += 1
    satisfaction_rate = n_satisfied / n_verdicts if n_verdicts else None

    avg_steps = sum(r["p4"]["step_count"] for r in summary_rows) / total if total else 0.0
    finish_count = sum(1 for r in summary_rows if r["p4"]["finish"])
    over_count = sum(1 for r in summary_rows if r["p4"]["over"])
    mean_max_repeats = sum(r["p4"]["max_consec_repeats"] for r in summary_rows) / total if total else 0.0

    print("========== 汇总 ==========")
    print(f"任务总数: {total}")
    print(
        f"[面板①] 成功率: {len(success)}/{total} ({len(success)/total*100:.1f}%) | "
        f"reward 均值: {reward_mean:.4f} | termination_reason 分布: {dict(term_dist)} | "
        f"撞对任务数: {len(collisions)}"
    )
    print(
        "[面板③] 五维度平均分: "
        + " ".join(f"{k}={avg_dims[k] if avg_dims[k] is not None else 'N/A'}" for k in JUDGE_DIMENSION_KEYS)
    )
    if satisfaction_rate is not None:
        print(f"[面板②] rubric 满足率: {n_satisfied} / {n_verdicts} = {satisfaction_rate*100:.1f}%")
    else:
        print("[面板②] rubric 满足率: 无 Judge 结果")
    print(
        f"[面板④] 平均步数: {avg_steps:.1f} | 主动 finish: {finish_count}/{total} | "
        f"over: {over_count}/{total} | 重复动作峰值均值: {mean_max_repeats:.2f}"
    )

    if args.out:
        summary = {
            "total": total,
            "success_count": len(success),
            "success_rate": len(success) / total if total else 0.0,
            "reward_mean": reward_mean,
            "termination_dist": dict(term_dist),
            "collision_count": len(collisions),
            "avg_dimensions": avg_dims,
            "rubric_satisfaction_rate": satisfaction_rate,
            "avg_steps": avg_steps,
            "finish_ratio": finish_count / total if total else 0.0,
            "over_ratio": over_count / total if total else 0.0,
            "mean_max_repeats": mean_max_repeats,
        }
        out = {"tasks": tasks_out, "summary": summary}
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"JSON 已写入 {args.out}")


if __name__ == "__main__":
    main()
