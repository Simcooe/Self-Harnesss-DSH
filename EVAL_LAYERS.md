# 评测的三层：结果 / 过程 / 答案

> 基于真实 trace：`runs/0827-1617/traces/0.model_trace.json` 与 `0.raw_trace.json`
> 任务 = 「帮我推荐一款适合5岁左右小孩的乳胶枕头…预算1000元以下」

先记住三句话定位：

- **结果层** = 买对没有 → `raw_trace` 终局步（step 13）的 `reward_detail`
- **过程层** = 怎么买的、有没有真核验 → `raw_trace` 非终局步（step 1~12）的 `progress`
- **答案层** = 标准答案长什么样 → `raw_trace` 终局步的 `goal` + `reset` 返回

`model_trace` 里没有过程层和答案层，它只有「模型看到的文本」+ 一个从结果层抽出来的 `terminal` 摘要。

---

## 0. 任务背景（答案层提前交代）

- **Query（模型拿到的提示词）**：`帮我推荐一款适合5岁左右小孩的乳胶枕头，要泰国生产的进口款，天然乳胶材质，摸起来更软、弹性更好，能保护颈部脊柱，带有满天星图案设计，预算在1000元以下。`
- **Gold 商品（标准答案）**：
  - `asin = 747848614498`（梦洁宝贝 泰国乳胶枕）
  - `attributes = [泰国, 进口, 天然, 护颈椎, 枕芯]`
  - `goal_options = ["【推荐4-6岁】塔拉蕾乳胶二阶枕：满天星"]`
  - `price_upper = 1000`

---

## 1. 结果层（Result）：买对没有

### model_trace 里的精简版 `terminal`

```json
"terminal": {
  "done": true,
  "termination_reason": "gold_purchase",
  "reward": 1,
  "reward_type": "gold_purchase",
  "purchase_success": true,
  "purchase": {
    "asin": "747848614498",
    "name": "梦洁宝贝泰国乳胶枕头进口天然乳胶枕护颈椎特拉雷儿童枕芯",
    "options": { "颜色分类": "【推荐4-6岁】塔拉蕾乳胶二阶枕：满天星" },
    "price": 999
  }
}
```

### raw_trace 终局步（step 13）的完整版 `reward_detail`

```json
"reward_detail": {
  "reward_type": "gold_purchase",
  "purchase_success": true,
  "target_asin_match": true,
  "weighted_score": 1,
  "dimension_scores": { "brand": 0, "core_functions": 1, "key_options": 1, "model": 0 },
  "hard_gates": {
    "budget":   { "actual": 999, "required": 1000, "passed": true },
    "category": { "actual": "床上用品›枕头›乳胶枕", "passed": true }
  }
}
```

### 直观解读

- 买到的 `purchase.asin = 747848614498`，等于 gold 的 asin → `target_asin_match = true`、满分 1。
- 两条硬约束（`hard_gates`）都过：**预算 999 ≤ 1000 ✅**，**类目「乳胶枕」匹配 ✅**。
- 4 个维度里 `core_functions=1`（5 个属性全中）、`key_options=1`（规格选对「4-6岁满天星」）；`brand=0`、`model=0` 是**不考核**（答案层会解释）。

> 这层只回答「结果对不对」，不回答「是不是瞎撞的」。

---

## 2. 过程层（Process）：有没有真核验

过程证据只存在于 **`raw_trace` 的 step 1~12 的 `progress.evidence_added`**（终局步没有 progress，见第 5 节澄清）。

| step | 动作 | evidence_added（过程证据） | 含义 |
|---|---|---|---|
| 1 | search 泰国进口 儿童乳胶枕头 满天星 | `result_set:3e13…` | 发起一个新搜索 |
| 2 | click 747848614498 | `product:747848614498` + `constraint:category:pass` + `constraint:budget:pass` | 打开了 gold 商品，并核验了类目和预算 |
| 3 | click description | `subpage:…:description` | 看了详情描述 |
| 4 | click features | `subpage:…:features` | 看了特性 |
| 5 | click reviews | `subpage:…:reviews` | 看了评价 |
| 6 | back to search | （空） | 返回，无新证据 |
| 7 | search PARATEX… | `result_set:17b0…` | 换关键词再搜 |
| 8 | click 643436000957 | `product:643436000957` + `constraint:budget:unverifiable` | 打开候选 PARATEX，发现预算无法核验（区间价） |
| 9 | back to search | （空） | 放弃这个候选 |
| 10 | search 梦洁宝贝… | `result_set:86b4…` | 再搜回目标品牌 |
| 11 | click 747848614498 | （空） | 重新打开（之前开过，不重复计） |
| 12 | click 【推荐4-6岁】…满天星 | `option:747848614498:color:…满天星` | 选了正确规格 |
| 13 | buy now | （无 progress） | 终局，不再产生过程证据 |

### 直观解读

- step 2 就打开了 gold 商品并核验了类目 + 预算 → 有 `product:` 和 `constraint:` 证据。
- step 3~5 看了三个子页 → 有 3 条 `subpage:` 证据，说明认真核验了商品信息。
- step 8 打开候选 `643436000957`，发现 `constraint:budget:unverifiable`（PARATEX 区间价 738~1533，没法确认 ≤1000），**主动放弃** —— 这是高质量行为。
- step 12 出现 `option:` 证据 → 证明**先选了规格再买**，不是瞎点 Buy Now。

> 这层回答「过程扎不扎实」。如果一条轨迹结果层满分、但过程层全空（比如 step1 直接撞到 asin 然后 buy now），就要判定是「撞对」，分数打折 —— 这正是要防的坑。

---

## 3. 答案层（Answer / TaskFacts）：标准答案

### raw_trace 终局步的 `goal`

```json
"goal": {
  "asin": "747848614498",
  "name": "梦洁宝贝泰国乳胶枕头进口天然乳胶枕护颈椎特拉雷儿童枕芯",
  "category": "床上用品›枕头›乳胶枕",
  "attributes": ["泰国","进口","天然","护颈椎","枕芯"],
  "expected_core_functions": ["泰国","进口","天然","护颈椎","枕芯"],
  "expected_brand": [],
  "expected_model": [],
  "goal_options": ["【推荐4-6岁】塔拉蕾乳胶二阶枕：满天星"],
  "price_upper": 1000,
  "instruction_text": "帮我推荐一款适合5岁左右小孩的乳胶枕头…",
  "instruction_simple": "给我推荐一款用天然乳胶做的乳胶枕，价格在1000元以内的。",
  "user_persona": { "品牌偏好": [{"品牌名称":"梦洁宝贝","偏好程度":"高"}, …] }
}
```

### reset 返回里提前落盘的部分答案

`reset.instruction` / `reset.goal_options` / `reset.instruction_simple` / `reset.user_persona`，对应 `raw_trace.reset` 和 `reset/<id>.json`。

### 直观解读

- `goal.asin` = 目标商品；`goal.goal_options` = 正确答案规格；`goal.price_upper` = 预算上限 1000；`goal.attributes` = 必须满足的属性。
- **关键**：`expected_brand=[]`、`expected_model=[]` 是空数组 → 这题 gold 根本没要求品牌和型号。这就是结果层里 `brand=0`、`model=0` 的原因 —— 不是「没做到」，是「不考核」。
- **答案层不能直接喂给 Judge**：`goal` 里有些信息（如 `user_persona` 品牌偏好「梦洁宝贝=高」）是 Query 里没提的，直接给 Judge 会把「没要求」误判成「没做到」。所以答案层只用来**生成 rubric**（提取 Hard 条件），不把 asin 和 goal_options 原文给 Judge。

---

## 4. 三层合起来怎么评

| 层 | 结论 | 真实依据 |
|---|---|---|
| 结果层 | ✅ 满分 | `purchase_success=true`、`weighted_score=1`、预算/类目硬约束全过 |
| 过程层 | ✅ 扎实 | 有 `product:` + `constraint:` + `option:` + 3 个 `subpage:`；还主动放弃了一个预算不可核验的候选 |
| 答案层 | 只做 rubric 原料 | Hard = {乳胶枕 / 预算1000 / 泰国进口天然 / 4-6岁满天星规格}；品牌是 Soft（本题 gold 甚至没要求） |

**最终结论**：这条是「真·满分」—— 结果对、过程也扎实。

**反面例子**：如果某条轨迹结果层也是 `purchase_success=true`，但过程层 `evidence_added` 从头到尾没有 `product:`、没有 `option:`、没有 `constraint:`（一步 search 直接撞到 asin 然后 buy now），那它就是「撞对」，最终分要大幅下调。

> 两层一起看，才能把「真会买」和「瞎撞对」分开。

---

## 5. 几个必须记住的澄清

### 5.1 step 13 没有 progress

`progress` 只在「这一步还没结束」时生成（`web_agent_text_env.py` 里 `if not status.get("done")`）。buy now 这一步 `done=True`，直接进入终局，所以**终局步没有 progress 字段**，过程层只看 step 1~12。

### 5.2 reset 的答案字段不是「泄露」给模型

`reset` 是任务开始前由 shell 脚本（`run_task.sh`）单独 curl 的初始化动作，发生在模型启动**之前**。它的返回里含 `goal_options` / `user_persona` / `instruction_simple` 等答案字段，但这些字段**只被落盘到 reset 文件，模型上下文里永远不会出现**。

模型从头到尾只看到两样东西：
1. 一开始的用户需求文本（Query 原文，= `instruction_text`）；
2. 每一步 interact 返回的 `instruction` 字段（当前页面文本观察）+ 可点击按钮列表。

类比：**reset = 赛前发卷子并锁进柜子；模型 = 只拿到题目，做题过程中看不到柜子里的答案；评测者 = 事后开柜子对答案。**

### 5.3 `dimension_scores` 里的 0 有两种含义

必须结合 `reward_detail.evidence.preference_scoring.dimensions` 里的 `active` 标志：

| 维度 | dimension_scores | active | passed/required | 含义 |
|---|---|---|---|---|
| `core_functions` | 1 | true | 5/5 | 真考核，满分 |
| `key_options` | 1 | true | 1/1 | 真考核，满分 |
| `brand` | 0 | false | 0/0 | 不考核（gold 没要求品牌） |
| `model` | 0 | false | 0/0 | 不考核（gold 没要求型号） |

- `active=true` 且 0 分 → 真失败（要求了但没满足）。
- `active=false` 的 0 分 → 不适用（本题没这个要求），**不是扣分**。

---

## 6. 字段速查

| 层 | 字段 | 出现在哪 |
|---|---|---|
| 结果层 | `reward` / `reward_detail`（`hard_gates` / `dimension_scores`）/ `purchase` | 终局步（step 13） |
| 过程层 | `progress`（`evidence_added` / `evidence_counts`） | 非终局步（step 1~12） |
| 答案层 | `goal`（`asin` / `goal_options` / `attributes` / `price_upper`） | 终局步（前面步是空 `{}`） |
| 答案层（部分） | `reset.goal_options` / `reset.instruction_simple` / `reset.user_persona` | reset 返回 |
| 任务提示词 | `instruction_text`（= trace 顶层 `task` = `reset.instruction`） | 全程 |

- **TaskFacts = `goal`**（终局步）。
- **实际任务提示词 = `goal.instruction_text`**（Query 原文，模型可见）。
- `instruction_simple` 是 gold 的简化摘要，属于答案一侧，模型看不到。
