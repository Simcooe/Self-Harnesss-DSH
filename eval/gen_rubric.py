#!/usr/bin/env python3
"""批量调 DeepSeek V4 Flash 生成 rubric，解析校验后冻结落盘 + manifest，支持断点续跑。

纯标准库，用系统 python3 运行：
  python3 eval/gen_rubric.py --goals eval/goals.jsonl --out eval/rubrics --max-tasks 6
"""
import argparse
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# 项目根目录下的 .env 提供 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = REPO_ROOT / ".env"

# rubric 生成模型配置：RUBRIC_* 为空时回退 DEEPSEEK_*
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"

SYSTEM_PROMPT = """# 任务：从「用户 Query + 目标商品标准答案」提炼购物评测 rubric

你是购物 Agent 评测的 rubric 生成器。输入是一条购物任务的用户需求（Query）和目标商品的标准答案（gold/TaskFacts）。输出一份「约束清单」，供后续 LLM Judge 评判 Agent 轨迹是否满足用户需求。

## 核心原则（必须遵守）

1. rubric 只反映「用户在 Query 里明确表达的要求」，不是照抄标准答案。
2. 标准答案里的信息，只有在「Query 明确提到、且可验证」时，才写成约束；Query 没提的，一律不要写进去（这是最重要的陷阱）。
3. 每条约束必须给出 Query 原文依据（query_quote），不能自己编造或扩展用户要求。
4. 不要把目标商品的 asin、goal_options 原文、价格具体数值照抄进 rubric——rubric 是自然语言规则，不是答案本身。

## 输出格式

输出 JSON，格式严格如下：
{
  "selected_constraints": [
    {
      "candidate_id": "c0001",
      "description": "自然语言描述的约束（一句话）",
      "hardness": "hard 或 soft",
      "query_quote": "Query 中的原话（逐字引用）",
      "selection_reason": "为什么选这条、为什么判 hard/soft（简短）"
    }
  ]
}

## hard 和 soft 的判定标准

- hard：Query 明确说出、且能在 goal 字段里找到可验证对应（如类目、产地、材质、功能、预算上限、适用人群、明确规格）。
- soft：Query 提到了，但是主观感受、无法用硬字段严格验证（如「摸起来更软」「更好看」「用起来方便」）。

## 陷阱规则（重点）

标准答案里常见的「Query 没提」的信息，不要写进 rubric：
1. 品牌偏好：如果 goal.expected_brand 是空数组 []，说明本题不要求品牌。即使 user_persona 里有「品牌偏好：某品牌=高」，也不写进 rubric。
2. 型号：expected_model 为空 → 不要求型号，不写。
3. 价格具体数值：rubric 写「预算 ≤ 1000 元」即可，不要写「目标商品价格是 999 元」。
4. goal_options 原文：不要写「正确规格是【推荐4-6岁】塔拉蕾乳胶二阶枕：满天星」，而是写「需要选择 4-6 岁适用的满天星图案规格」这类自然语言。

## 完整示例

输入：
{
  "query": "帮我推荐一款适合5岁左右小孩的乳胶枕头，要泰国生产的进口款，天然乳胶材质，摸起来更软、弹性更好，能保护颈部脊柱，带有满天星图案设计，预算在1000元以下。",
  "goal": {
    "category": "床上用品›枕头›乳胶枕",
    "attributes": ["泰国", "进口", "天然", "护颈椎", "枕芯"],
    "expected_core_functions": ["泰国", "进口", "天然", "护颈椎", "枕芯"],
    "expected_brand": [],
    "expected_model": [],
    "goal_options": ["【推荐4-6岁】塔拉蕾乳胶二阶枕：满天星"],
    "price_upper": 1000,
    "user_persona": { "品牌偏好": [{"品牌名称": "梦洁宝贝", "偏好程度": "高"}] }
  }
}

输出：
{
  "selected_constraints": [
    {"candidate_id": "c0001", "description": "商品品类为乳胶枕", "hardness": "hard", "query_quote": "乳胶枕头", "selection_reason": "Query 明确提出了商品类型"},
    {"candidate_id": "c0002", "description": "适合5岁左右儿童使用", "hardness": "hard", "query_quote": "适合5岁左右小孩", "selection_reason": "Query 明确提出了适用人群"},
    {"candidate_id": "c0003", "description": "产地为泰国、进口", "hardness": "hard", "query_quote": "泰国生产的进口款", "selection_reason": "Query 明确提出了产地与进口属性"},
    {"candidate_id": "c0004", "description": "材质为天然乳胶", "hardness": "hard", "query_quote": "天然乳胶材质", "selection_reason": "Query 明确提出了材质"},
    {"candidate_id": "c0005", "description": "具备护颈椎功能", "hardness": "hard", "query_quote": "能保护颈部脊柱", "selection_reason": "Query 明确提出了功能要求"},
    {"candidate_id": "c0006", "description": "带有满天星图案", "hardness": "hard", "query_quote": "带有满天星图案设计", "selection_reason": "Query 明确提出了图案"},
    {"candidate_id": "c0007", "description": "价格在1000元以内", "hardness": "hard", "query_quote": "预算在1000元以下", "selection_reason": "Query 明确提出了预算上限"},
    {"candidate_id": "c0008", "description": "柔软、弹性好", "hardness": "soft", "query_quote": "摸起来更软、弹性更好", "selection_reason": "主观感受，无法用硬字段严格验证"}
  ]
}

注意：示例中 user_persona 的「品牌偏好：梦洁宝贝=高」没有被写进 rubric，因为 expected_brand 是空的，品牌不是本题要求。

## 执行要求
- 只输出 JSON，不要输出解释性文字。
- 约束数量以 Query 实际要求为准，通常 5~10 条，不为了凑数硬拆。
- 去重：同一要求不重复列出。
- candidate_id 从 c0001 递增。"""

REQUIRED_FIELDS = ["description", "hardness", "query_quote", "selection_reason"]


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


def _parse_json_content(content):
    """先直接 json.loads，失败则剥离首尾非 JSON 文本。"""
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


def parse_and_validate(content):
    obj = _parse_json_content(content)
    if not isinstance(obj, dict):
        raise ValueError("顶层不是 JSON 对象")
    cons = obj.get("selected_constraints")
    if not isinstance(cons, list):
        raise ValueError("selected_constraints 不是 list")
    kept = []
    dropped = 0
    for c in cons:
        if not isinstance(c, dict):
            dropped += 1
            continue
        if not all(k in c for k in REQUIRED_FIELDS):
            dropped += 1
            continue
        if c.get("hardness") not in ("hard", "soft"):
            dropped += 1
            continue
        kept.append(c)
    if not kept:
        raise ValueError("没有通过校验的约束条目")
    return {"selected_constraints": kept}, dropped


def call_flash(api_key, base_url, model, messages, include_response_format):
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": 0}
    if include_response_format:
        # 若代理不支持 response_format 会报错，重试时去掉
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
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = resp.read().decode("utf-8")
    obj = json.loads(body)
    return obj["choices"][0]["message"]["content"]


def generate_one(api_key, base_url, model, task):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(
            {"query": task["query"], "goal": task["goal"]}, ensure_ascii=False
        )},
    ]
    last_err = None
    for attempt in range(3):
        # 第一次带 response_format，后续去掉（容错代理不支持）
        include_rf = attempt == 0
        try:
            content = call_flash(api_key, base_url, model, messages, include_rf)
            rubric, dropped = parse_and_validate(content)
            return rubric, dropped, None
        except Exception as e:
            last_err = e
    return None, 0, last_err


def load_goals(path):
    tasks = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    return tasks


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
        if d.get("frozen") is True and d.get("task_id"):
            frozen.add(d["task_id"])
    return frozen


def resolve_model_config(env, prefix, default_model):
    """按回退规则解析模型配置：<PREFIX>_* 为空时回退 DEEPSEEK_*。"""
    api_key = env.get(f"{prefix}_API_KEY") or env.get("DEEPSEEK_API_KEY")
    base_url = (
        env.get(f"{prefix}_BASE_URL")
        or env.get("DEEPSEEK_BASE_URL")
        or DEFAULT_BASE_URL
    )
    model = env.get(f"{prefix}_MODEL") or default_model
    return api_key, base_url, model


def main():
    ap = argparse.ArgumentParser(description="批量生成并冻结 rubric")
    ap.add_argument("--goals", required=True, help="gen_goals.py 输出的 JSONL")
    ap.add_argument("--out", required=True, help="rubric 输出目录")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--max-tasks", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="覆盖已冻结的 rubric")
    ap.add_argument("--env", default=str(DEFAULT_ENV), help=".env 路径")
    args = ap.parse_args()

    env = load_env(args.env)
    api_key, base_url, model = resolve_model_config(env, "RUBRIC", DEFAULT_MODEL)
    if not api_key:
        print("缺少 RUBRIC_API_KEY / DEEPSEEK_API_KEY（检查 .env）", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks = load_goals(args.goals)
    if args.max_tasks is not None:
        tasks = tasks[: args.max_tasks]

    frozen_ids = scan_frozen(out_dir)
    skipped = []
    to_generate = []
    for t in tasks:
        if not args.force and t["task_id"] in frozen_ids:
            skipped.append(t["task_id"])
        else:
            to_generate.append(t)

    print(f"总任务 {len(tasks)}，跳过(已冻结) {len(skipped)}，待生成 {len(to_generate)}")

    succeeded = []
    failed = []

    def run(t):
        tid = t["task_id"]
        rubric, dropped, err = generate_one(api_key, base_url, model, t)
        return tid, t["query"], rubric, dropped, err

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = {ex.submit(run, t): t for t in to_generate}
        for fut in as_completed(futures):
            tid, query, rubric, dropped, err = fut.result()
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
                "rubric": rubric,
            }
            (out_dir / f"{tid}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            succeeded.append(tid)
            if dropped:
                print(f"[{tid}] 成功，丢弃 {dropped} 条不合法约束", file=sys.stderr)

    # 跳过(已冻结)的 task 是既往成功，并入 succeeded，避免重跑后 manifest 丢失成功记录
    manifest = {
        "total": len(tasks),
        "succeeded": succeeded + skipped,
        "failed": failed,
        "skipped": skipped,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"完成：成功 {len(succeeded)}，失败 {len(failed)}，跳过 {len(skipped)}")
    print(f"manifest -> {out_dir / 'manifest.json'}")


if __name__ == "__main__":
    main()
