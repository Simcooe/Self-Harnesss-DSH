#!/usr/bin/env python3
"""离线确定性评测：读 raw_trace.json，输出结果层/过程层/失败归类/确定性指标报告。"""
import argparse
import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


# 循环触发阈值：与 simulator 的 repeat_loop 触发条件保持一致（no_progress 达到 4）
LOOP_CONSEC_REPEATS_THRESHOLD = 3
LOOP_NO_PROGRESS_THRESHOLD = 4
# 「搜索太宽」判定：去重后的 result_set 数量不超过该值视为搜索过窄
NARROW_SEARCH_RESULT_SET_MAX = 1


def load_trace(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def final_raw(trace):
    steps = trace.get("steps", [])
    if not steps:
        return {}
    return steps[-1].get("raw", {}) or {}


def collect_process(trace):
    """汇总全程 evidence_added 与循环/终止信号。progress 只在非终局步存在，逐条容错。"""
    evidence = defaultdict(list)  # type -> [完整 token]
    product_asins = []
    max_consec_repeats = 0
    max_no_progress = 0
    finish = False
    gold_in_observation = False
    for s in trace.get("steps", []):
        tn = (s.get("tool_name") or "").lower()
        if tn in ("finish", "stop", "submit") or "finish" in tn or "abstain" in tn:
            finish = True
        raw = s.get("raw", {}) or {}
        p = raw.get("progress")
        if not isinstance(p, dict):
            continue
        for e in p.get("evidence_added") or []:
            typ = e.split(":", 1)[0]
            evidence[typ].append(e)
            if typ == "product":
                asin = e.split(":", 1)[1]
                if asin not in product_asins:
                    product_asins.append(asin)
        max_consec_repeats = max(max_consec_repeats, p.get("consecutive_repeats", 0) or 0)
        max_no_progress = max(max_no_progress, p.get("no_progress_steps", 0) or 0)
    return evidence, product_asins, max_consec_repeats, max_no_progress, finish


def gold_seen_in_observations(trace, gold_asin):
    """gold 是否曾出现在模型观察文本里 —— 作为「找到但没打开」的判定依据。"""
    if not gold_asin:
        return False
    for s in trace.get("steps", []):
        instr = (s.get("raw", {}) or {}).get("instruction") or ""
        if gold_asin in instr:
            return True
    return False


def compute_collision(purchase_success, goal, hard_gates, evidence, product_asins):
    """goal 感知的撞对检测。

    撞对 = 买对了（purchase_success），但缺了「本题本该有」的核验证据。
    「本该有」由 goal 决定，不是写死三样全缺：
      - option:   goal 有规格（goal_options / required_options_by_key 非空）时才必需
      - constraint: hard_gates 有 budget / category 时才必需
    product 恒必需（不打开商品页到不了 Buy Now），但「打开商品」不构成核验，不单独判。
    """
    if not purchase_success:
        return False, ""
    missing = []
    has_options = bool(goal.get("goal_options") or goal.get("required_options_by_key"))
    if has_options and not evidence.get("option"):
        missing.append("option（有规格要求但未选规格）")
    has_gates = bool(hard_gates)
    if has_gates and not evidence.get("constraint"):
        missing.append("constraint（有硬门槛但未核验）")
    if missing:
        return True, "结果满分，但缺: " + "、".join(missing)
    return False, ""


def classify_failure(term, ev, product_asins, reward_type, purchase_success, hard_gates,
                     gold_asin, gold_seen, max_cr, max_np, finish, over, n_result_sets,
                     purchase_asin=None):
    """按优先级归入失败类别，返回 (中文标签, 依据)；成功返回 None。"""
    if reward_type == "gold_purchase" or purchase_success:
        return None
    t = (term or "").lower()
    if reward_type == "partial_alternative_purchase":
        return "买错商品（替代品购买）", f"购买 {purchase_asin}，但 gold 是 {gold_asin}"
    if finish or "abstain" in t:
        return "主动放弃", f"出现 finish 动作或 termination_reason 含 abstain（term={term}）"
    if (hard_gates.get("budget") or {}).get("passed") is False:
        return "超预算", "hard_gates.budget.passed == false"
    if (hard_gates.get("category") or {}).get("passed") is False:
        return "类目不符", "hard_gates.category.passed == false"
    if "repeat" in t or "no_progress" in t or max_cr >= LOOP_CONSEC_REPEATS_THRESHOLD or max_np >= LOOP_NO_PROGRESS_THRESHOLD:
        return "陷入循环", f"term={term}, max_consec_repeats={max_cr}, max_no_progress={max_np}"
    # 注意：over 在成功样本里也为 True（表示本局结束），不能单凭 over 判超限。
    if "max_steps" in t or "context" in t or "max_" in t or (over and not t):
        return "超步数/上下文超限", f"term={term}, over={over}"
    if gold_asin:
        if gold_seen and gold_asin not in product_asins:
            return "找到但没打开", f"gold={gold_asin} 出现在观察文本中，但全程无 product:{gold_asin} 证据"
        if product_asins and not ev.get("option"):
            return "打开但没核验规格", "有 product 证据但无 option 证据"
    if gold_asin and gold_asin not in product_asins and n_result_sets <= NARROW_SEARCH_RESULT_SET_MAX:
        return "搜索太宽", "gold 从未出现在 product 证据中且 result_set 数量少"
    return "其它", f"termination_reason={term}"


def fmt_bool(v):
    return "true" if v else "false"


def report_one(trace, path):
    final = final_raw(trace)
    rd = final.get("reward_detail") or {}
    goal = final.get("goal") or {}
    purchase = final.get("purchase") or {}

    reward = (rd.get("reward") if isinstance(rd, dict) and rd.get("reward") is not None
              else final.get("reward"))
    reward_type = rd.get("reward_type")
    purchase_success = rd.get("purchase_success")
    target_asin_match = rd.get("target_asin_match")
    reward_valid = final.get("reward_valid") if final.get("done") else None
    term = final.get("termination_reason")
    over = bool(final.get("over"))
    hard_gates = rd.get("hard_gates") or {}
    dim_scores = rd.get("dimension_scores") or {}
    dims_active = {}
    pref = (rd.get("evidence") or {}).get("preference_scoring") or {}
    for name, d in (pref.get("dimensions") or {}).items():
        dims_active[name] = bool(d.get("active"))

    ev, product_asins, max_cr, max_np, finish = collect_process(trace)
    gold_asin = goal.get("asin")
    gold_seen = gold_seen_in_observations(trace, gold_asin)
    n_result_sets_total = len(ev.get("result_set", []))
    n_result_sets_distinct = len(set(ev.get("result_set", [])))
    n_products = len(product_asins)
    n_options = len(ev.get("option", []))
    n_constraints = len(ev.get("constraint", []))
    n_subpages = len(ev.get("subpage", []))

    lines = []
    lines.append("=" * 72)
    lines.append(f"任务: {path}")
    lines.append(f"需求: {trace.get('task', '')}")
    lines.append("-" * 72)

    # 1. 结果层
    lines.append("[结果层]")
    lines.append(f"  reward: {reward}")
    lines.append(f"  reward_type: {reward_type}")
    lines.append(f"  purchase_success: {fmt_bool(purchase_success)}")
    lines.append(f"  target_asin_match: {fmt_bool(target_asin_match)}")
    lines.append(f"  reward_valid: {fmt_bool(reward_valid) if reward_valid is not None else '无'}")
    if hard_gates:
        lines.append("  hard_gates:")
        for k, v in hard_gates.items():
            lines.append(f"    {k}: {'pass' if v.get('passed') else 'fail'}")
    else:
        lines.append("  hard_gates: (无)")
    if dim_scores:
        lines.append("  dimension_scores:")
        for name in ("brand", "core_functions", "key_options", "model"):
            if name not in dim_scores:
                continue
            score = dim_scores[name]
            active = dims_active.get(name)
            if active is True and score == 0:
                tag = "真失败"
            elif active is False and score == 0:
                tag = "不考核（本题无此要求）"
            else:
                tag = "正常"
            lines.append(f"    {name}: {score}  [{tag}]")
    else:
        lines.append("  dimension_scores: (无)")

    # 2. 过程层（撞对检测）
    collision, collision_reason = compute_collision(purchase_success, goal, hard_gates, ev, product_asins)
    lines.append("[过程层]")
    lines.append(f"  打开过商品 (product): {'是' if product_asins else '否'}  ({n_products} 个去重 asin)")
    lines.append(f"  选过规格 (option): {'是' if n_options else '否'}  ({n_options} 条)")
    lines.append(f"  核验过硬约束 (constraint): {'是' if n_constraints else '否'}  ({n_constraints} 条)")
    lines.append(f"  看过子页 (subpage): {'是' if n_subpages else '否'}  ({n_subpages} 条)")
    if collision:
        lines.append(f"  撞对标记: 疑似撞对 —— {collision_reason}")
    else:
        lines.append("  撞对标记: 无")

    # 3. 失败归类
    lines.append("[失败归类]")
    cls = classify_failure(term, ev, product_asins, reward_type, purchase_success, hard_gates,
                           gold_asin, gold_seen, max_cr, max_np, finish, over, n_result_sets_distinct,
                           purchase_asin=(purchase or {}).get("asin"))
    if cls is None:
        lines.append("  类别: 成功（gold_purchase / purchase_success=true），不归类")
    else:
        label, basis = cls
        lines.append(f"  类别: {label}")
        lines.append(f"  依据: {basis}")

    # 4. 确定性指标
    lines.append("[确定性指标]")
    lines.append(f"  步数: {trace.get('step_count')}")
    lines.append(f"  搜索次数: {n_result_sets_total} 条 (去重 {n_result_sets_distinct} 次)")
    lines.append(f"  打开商品数: {n_products}")
    lines.append(f"  选择规格次数: {n_options}")
    lines.append(f"  子页查看次数: {n_subpages}")
    lines.append(f"  最大连续重复动作: {max_cr}")
    lines.append(f"  主动 finish: {'是' if finish else '否'}")
    lines.append(f"  over: {fmt_bool(over)}")

    return "\n".join(lines), {
        "path": path,
        "reward": reward if isinstance(reward, (int, float)) else 0.0,
        "reward_type": reward_type,
        "purchase_success": bool(purchase_success),
        "term": term,
        "step_count": trace.get("step_count"),
        "collision": collision,
        "class_label": cls[0] if cls else "成功",
    }


def collect_paths(args):
    paths = []
    seen = set()
    for a in args:
        p = Path(a)
        if p.is_dir():
            files = sorted(glob.glob(str(p / "*.raw_trace.json")))
        else:
            files = [str(p)]
        for f in files:
            if f not in seen:
                seen.add(f)
                paths.append(f)
    return paths


def main():
    ap = argparse.ArgumentParser(description="离线评测 raw_trace.json")
    ap.add_argument("--traces", nargs="+", required=True,
                    help="trace 目录或单个 *.raw_trace.json 文件，可多个")
    args = ap.parse_args()

    paths = collect_paths(args.traces)
    if not paths:
        print("未找到任何 raw_trace.json", file=sys.stderr)
        sys.exit(1)

    summaries = []
    for path in paths:
        try:
            trace = load_trace(path)
        except Exception as e:
            print(f"[跳过] {path}: 解析失败 {e}", file=sys.stderr)
            continue
        text, summ = report_one(trace, path)
        print(text)
        print()
        summaries.append(summ)

    if not summaries:
        sys.exit(1)

    # 汇总
    total = len(summaries)
    success = [s for s in summaries if s["purchase_success"] or s["reward_type"] == "gold_purchase"]
    rewards = [s["reward"] for s in summaries]
    avg_reward = sum(rewards) / total if total else 0.0
    avg_steps = sum(s["step_count"] for s in summaries) / total if total else 0.0
    term_counter = Counter((s["term"] or "(none)") for s in summaries)
    class_counter = Counter(s["class_label"] for s in summaries)
    collisions = [s["path"] for s in summaries if s["collision"]]

    print("=" * 72)
    print("[汇总]")
    print(f"  总任务数: {total}")
    print(f"  成功数: {len(success)}")
    print(f"  成功率: {len(success) / total * 100:.1f}%")
    print(f"  reward 均值: {avg_reward:.4f}")
    print(f"  平均步数: {avg_steps:.1f}")
    print("  termination_reason 分布:")
    for term, cnt in term_counter.most_common():
        print(f"    {term}: {cnt}")
    print(f"  撞对任务数: {len(collisions)}")
    for c in collisions:
        print(f"    - {c}")
    print("  失败类别计数:")
    for label, cnt in sorted(class_counter.items()):
        print(f"    {label}: {cnt}")


if __name__ == "__main__":
    main()
