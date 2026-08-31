#!/usr/bin/env python3
"""LLM Judge 软性评测：读冻结 rubric + 模型轨迹，判四态 + 五维度，冻结落盘 + manifest，支持断点续跑。

纯标准库，用系统 python3 运行：
  python3 eval/judge.py --rubrics eval/rubrics --traces runs/0827-1617/traces --out eval/judgments --max-tasks 6
"""
import argparse
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# 项目根目录下的 .env 提供 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = REPO_ROOT / ".env"

DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_BASE_URL = "https://api.deepseek.com"

# 代理内容审核的拒答特征：出现这类文本说明触发了审核，应重试而不是当解析失败
REFUSAL_MARKERS = ("当前输入涉及敏感信息", "换个话题", "无法回答", "不能回答", "抱歉")

DIMENSION_KEYS = [
    "search_strategy",
    "candidate_utilization",
    "evidence_verification",
    "decision_quality",
    "termination_efficiency",
]
FOUR_STATES = {"satisfied", "violated", "unknown", "not_applicable"}

JUDGE_SYSTEM_PROMPT = """# 任务：评判购物 Agent 的一条执行轨迹

你是购物 Agent 评测的 LLM Judge。输入包含：用户 Query、冻结的评测 rubric、Agent 的完整执行轨迹。
你需要从两个角度给出结构化评分。

## 输入说明
- Query：用户需求原文
- rubric：一组约束（每条含 candidate_id / description / hardness / query_quote）
- trajectory：Agent 每一步的「动作 + 观察文本」，每步带 Step 序号

## 角度一：逐条 rubric 判四态

对 rubric 里每一条约束，判定其状态：
- satisfied：轨迹中有明确证据表明该要求被满足
- violated：轨迹中有明确证据表明该要求被违反
- unknown：轨迹中没有足够证据判断
- not_applicable：该要求不适用于当前任务

判四态时注意：
- 你只评判「轨迹的 Observation 文本中是否出现了与约束描述相匹配的证据」，不要推断任务最终是否购买成功。
- 轨迹最后一步可能出现「Episode finished.」。这只是环境的终局提示，不代表购买成功，也不携带任何结果信息；不要据此判定任何约束为 satisfied 或 violated。
- 若某条约束在轨迹的 Observation 文本中找不到明确证据，判 unknown；不要因为「看起来合理」就判 satisfied。

每个判定必须包含：
1. status（上述四选一）
2. step_reference（一个或多个 Step 序号，指向支撑你判断的具体动作；无依据时给 []）
3. reasoning（一句话说明依据）

## 角度二：五个维度过程质量打分

对整条轨迹，在五个维度各打 0/1/2 分：
- search_strategy（搜索策略）：搜索词是否合理、是否收敛、是否避免无效重复搜索
- candidate_utilization（候选利用）：是否打开并利用候选商品、是否对比多个候选
- evidence_verification（证据核验）：是否打开详情/子页核验属性、规格
- decision_quality（决策质量）：购买/放弃决策是否符合需求，是否避免随意购买或错误放弃
- termination_efficiency（终止效率）：是否在合适时机结束，不过早放弃、不过度搜索、避免重复和超步数

打分标准：
- 0：存在明显问题，或基本没有完成该维度的要求
- 1：部分完成，但仍有一些错误或低效行为
- 2：整体表现合理，没有明显问题

## 输出格式

只输出 JSON，严格如下：
{
  "dimension_scores": {
    "search_strategy": 0,
    "candidate_utilization": 0,
    "evidence_verification": 0,
    "decision_quality": 0,
    "termination_efficiency": 0
  },
  "rubric_verdicts": [
    {
      "candidate_id": "c0001",
      "status": "satisfied",
      "step_reference": [2, 13],
      "reasoning": "step2 打开商品页，step13 的 Observation 中出现匹配的规格与属性证据"
    }
  ]
}

## 执行要求
- 只输出 JSON，不要输出解释性文字。
- rubric_verdicts 必须覆盖 rubric 里的每一条约束，不遗漏。
- status 只能取 satisfied / violated / unknown / not_applicable 之一。
- dimension_scores 每项只能取 0 / 1 / 2。
- step_reference 必须指向 Observation 中实际出现该证据的 Step（整数）；找不到证据的约束，status 必须为 unknown 且 step_reference 为 []。"""


def load_env(path):
    env = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        env[k.strip()] = v
    return env


def resolve_model_config(env, prefix, default_model):
    api_key = env.get(f"{prefix}_API_KEY") or env.get("DEEPSEEK_API_KEY")
    base_url = (
        env.get(f"{prefix}_BASE_URL")
        or env.get("DEEPSEEK_BASE_URL")
        or DEFAULT_BASE_URL
    )
    model = env.get(f"{prefix}_MODEL") or default_model
    return api_key, base_url, model


def _parse_json_content(content):
    if isinstance(content, (dict, list)):
        return content
    text = content.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("content 里找不到 JSON 对象")
        return json.loads(text[start : end + 1])


def parse_and_validate(content, expected_ids):
    obj = _parse_json_content(content)
    if not isinstance(obj, dict):
        raise ValueError("顶层不是 JSON 对象")
    dims = obj.get("dimension_scores")
    if not isinstance(dims, dict):
        raise ValueError("dimension_scores 不是 dict")
    for k in DIMENSION_KEYS:
        if dims.get(k) not in (0, 1, 2):
            raise ValueError(f"dimension_scores.{k} 不在 0/1/2 内")
    verdicts = obj.get("rubric_verdicts")
    if not isinstance(verdicts, list) or not verdicts:
        raise ValueError("rubric_verdicts 不是非空 list")
    kept = []
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        if v.get("status") not in FOUR_STATES:
            continue
        if not isinstance(v.get("step_reference"), list):
            continue
        if "candidate_id" not in v or "reasoning" not in v:
            continue
        kept.append(v)
    if not kept:
        raise ValueError("没有通过校验的 verdict 条目")
    got_ids = {v["candidate_id"] for v in kept}
    missing = expected_ids - got_ids
    return (
        {
            "dimension_scores": {k: dims[k] for k in DIMENSION_KEYS},
            "rubric_verdicts": kept,
        },
        missing,
    )


def call_judge(api_key, base_url, model, messages, include_response_format):
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": 0}
    if include_response_format:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        body = resp.read().decode("utf-8")
    obj = json.loads(body)
    return obj["choices"][0]["message"]["content"]


def serialize_trajectory(steps, query="", obs_max_chars=1000):
    parts = []
    for s in steps:
        tool_name = s.get("tool_name") or ""
        tool_args = s.get("tool_args") or {}
        if isinstance(tool_args, dict):
            args_str = " ".join(f'{k}="{v}"' for k, v in tool_args.items())
        else:
            args_str = str(tool_args)
        obs = s.get("observation") or ""
        # observation 每步都重复嵌入完整 query，纯噪声且拉长正文；去掉。
        if query and f"Instruction: [SEP] {query} [SEP] " in obs:
            obs = obs.replace(f"Instruction: [SEP] {query} [SEP] ", "")
        # 截断只控制 token 长度，不承担审核规避职责；过短会截掉证据导致误判 unknown。
        if len(obs) > obs_max_chars:
            obs = obs[:obs_max_chars] + "…[truncated]"
        parts.append(
            f"Step {s.get('step')} [{tool_name}] {args_str}\nObservation: {obs}"
        )
    return "\n\n".join(parts)


def judge_one(api_key, base_url, model, query, constraints, steps,
              obs_max_chars=1000, refused_path=None):
    expected_ids = {c.get("candidate_id") for c in constraints if c.get("candidate_id")}
    user_content = json.dumps(
        {
            "query": query,
            "rubric": {"selected_constraints": constraints},
            "trajectory": serialize_trajectory(steps, query, obs_max_chars),
        },
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    last_err = None
    for attempt in range(3):
        include_rf = attempt == 0
        try:
            content = call_judge(api_key, base_url, model, messages, include_rf)
            if any(m in content for m in REFUSAL_MARKERS):
                # 内容型拒答是确定性的，重试无效；把当次输入落盘方便定位触发词
                if refused_path:
                    Path(refused_path).write_text(user_content, encoding="utf-8")
                raise RuntimeError(f"内容审核拒答: {content[:80]}")
            judgment, missing = parse_and_validate(content, expected_ids)
            return judgment, missing, None
        except Exception as e:
            last_err = e
            # 审核误触发往往瞬时，退避后重试有较高概率恢复
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    return None, None, last_err


def find_trace_ids(traces_dir):
    """遍历 traces 目录下 <N>.model_trace.json，返回排序后的 N 列表。"""
    traces_dir = Path(traces_dir)
    if not traces_dir.is_dir():
        return []
    ids = []
    for f in traces_dir.glob("*.model_trace.json"):
        stem = f.name.split(".model_trace.json")[0]
        if stem.isdigit():
            ids.append(stem)
    return sorted(ids, key=int)


def scan_frozen(out_dir):
    frozen = set()
    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        return frozen
    for f in out_dir.glob("*.json"):
        if f.name == "manifest.json":
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("frozen") is True and d.get("task_id") is not None:
            frozen.add(str(d["task_id"]))
    return frozen


def main():
    ap = argparse.ArgumentParser(description="LLM Judge 软性评测")
    ap.add_argument("--rubrics", required=True, help="rubric 目录")
    ap.add_argument("--traces", required=True, help="trace 目录")
    ap.add_argument("--out", required=True, help="judgment 输出目录")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--max-tasks", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="覆盖已冻结的 judgment")
    ap.add_argument("--obs-max-chars", type=int, default=1000,
                    help="每步 observation 截断长度")
    ap.add_argument("--env", default=str(DEFAULT_ENV), help=".env 路径")
    args = ap.parse_args()

    env = load_env(args.env)
    api_key, base_url, model = resolve_model_config(env, "JUDGE", DEFAULT_MODEL)
    if not api_key:
        print("缺少 JUDGE_API_KEY / DEEPSEEK_API_KEY（检查 .env）", file=sys.stderr)
        sys.exit(1)

    rubrics_dir = Path(args.rubrics)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    trace_ids = find_trace_ids(args.traces)
    if args.max_tasks is not None:
        trace_ids = trace_ids[: args.max_tasks]

    frozen_ids = scan_frozen(out_dir)
    skipped = []
    to_judge = []
    missing_rubric = []
    for tid in trace_ids:
        if not args.force and tid in frozen_ids:
            skipped.append(tid)
            continue
        if not (rubrics_dir / f"{tid}.json").exists():
            missing_rubric.append(tid)
            continue
        to_judge.append(tid)

    print(
        f"trace 总数 {len(trace_ids)}，跳过(已冻结) {len(skipped)}，"
        f"缺 rubric {len(missing_rubric)}，待判 {len(to_judge)}"
    )
    if missing_rubric:
        print(f"[warn] 缺 rubric 的 task_id: {missing_rubric}", file=sys.stderr)

    succeeded = []
    failed = []

    def run(tid):
        rubric = json.loads((rubrics_dir / f"{tid}.json").read_text(encoding="utf-8"))
        trace = json.loads((Path(args.traces) / f"{tid}.model_trace.json").read_text(encoding="utf-8"))
        query = rubric.get("query") or trace.get("task") or ""
        constraints = (rubric.get("rubric") or {}).get("selected_constraints") or []
        steps = trace.get("steps") or []
        refused_path = out_dir / f".refused-{tid}.json"
        judgment, missing, err = judge_one(
            api_key, base_url, model, query, constraints, steps,
            obs_max_chars=args.obs_max_chars, refused_path=str(refused_path),
        )
        return tid, query, judgment, missing, err

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = {ex.submit(run, tid): tid for tid in to_judge}
        for fut in as_completed(futures):
            tid, query, judgment, missing, err = fut.result()
            if err is not None:
                failed.append(tid)
                print(f"[失败] {tid}: {err}", file=sys.stderr)
                continue
            rec = {
                "task_id": tid,
                "query": query,
                "frozen": True,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "model": model,
                "judgment": judgment,
            }
            (out_dir / f"{tid}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            succeeded.append(tid)
            if missing:
                print(f"[{tid}] 成功，但 rubric 缺失 {sorted(missing)} 条 verdict", file=sys.stderr)

    manifest = {
        "total": len(trace_ids),
        "succeeded": succeeded + skipped,
        "failed": failed,
        "skipped": skipped,
        "missing_rubric": missing_rubric,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"完成：成功 {len(succeeded)}，失败 {len(failed)}，跳过 {len(skipped)}")
    print(f"manifest -> {out_dir / 'manifest.json'}")


if __name__ == "__main__":
    main()
