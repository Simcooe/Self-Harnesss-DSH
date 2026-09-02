# 评测全流程走查（以 task 0 为例）

> 用一条成功样本贯穿：**task 0（乳胶枕，`runs/0827-1617/traces/0`）**
> 结局：`gold_purchase`、`reward=1`、`purchase_success=true`、13 步、过程证据齐全。

字段旁直接标注解释。字段完整路径标注来源文件（`raw_trace` / `model_trace` / 离线数据集）。

前提：task 0 的 judge 结果当前缺失（内容审核拒答），judge 部分说明「读什么、怎么判」，末尾标注。

---

## 一、hard gate（环境硬门槛）

**读 `raw_trace` 终局步**，环境内部比对两组字段，产出 `reward_detail.hard_gates`。

| 门槛 | 环境读的字段（完整路径） | 解释 | task 0 值 | 结果 |
|---|---|---|---|---|
| category | `raw_trace.steps[13].raw.purchase.category` | 买到的商品类目 | `床上用品›枕头›乳胶枕` | pass |
| | vs `raw_trace.steps[13].raw.goal.category` | 目标商品类目（gold） | 同上 | |
| budget | `raw_trace.steps[13].raw.reward_detail.evidence.price_resolution.price` | 成交价（选规格后） | `999` | pass |
| | vs `raw_trace.steps[13].raw.goal.price_upper` | 预算上限（从 Query "预算1000以下" 解析出） | `1000` | |

产出字段：

```
raw_trace.steps[13].raw.reward_detail.hard_gates.budget.passed    = true   ← 预算门槛是否通过
raw_trace.steps[13].raw.reward_detail.hard_gates.category.passed  = true   ← 类目门槛是否通过
```

**作用**：reward 的一票否决。两个都 pass，才继续算四维软分。

---

## 二、reward 如何计算（环境 `reward.py`，task 0 走完整购买路径）

reward 只在终局算一次。task 0 走 `evaluate_purchase`（购买路径），分四步：

### 第 1 步：两个 hard gate（即第一节）

`budget=pass`、`category=pass` → 通过，继续往下。

### 第 2 步：四维软匹配（读 `raw_trace.steps[13].raw.goal` 的要求字段）

四个维度，每个有 `active`（是否考核）和 `score`（通过数/要求数），权重 `DIMENSION_WEIGHTS`：brand=0.35、model=0.25、core_functions=0.25、key_options=0.15。

| 维度 | 要求字段（完整路径） | 权重 | active | 通过/要求 | score |
|---|---|---|---|---|---|
| brand | `goal.expected_brand`（空数组） | 0.35 | **false**（没要求品牌） | 0/0 | 0 |
| model | `goal.expected_model`（空数组） | 0.25 | **false**（没要求型号） | 0/0 | 0 |
| core_functions | `goal.expected_core_functions` = `["泰国","进口","天然","护颈椎","枕芯"]` | 0.25 | **true** | 5/5 | 1 |
| key_options | `goal.required_options_by_key` = `{"color":{...满天星}}` | 0.15 | **true** | 1/1 | 1 |

**match_score**（加权分，只对 active 维度算）：

```
match_score = (0.25×1 + 0.15×1) / (0.25+0.15) = 1.0
```

落在 `raw_trace.steps[13].raw.reward_detail.weighted_score = 1.0`。

### 第 3 步：决策树定 reward_type（读 `purchase.asin` vs `goal.asin`）

```
1. hard gate 有 unverifiable? 否
2. hard gate 有 fail? 否
3. 四维全满足(all_satisfied=true)?
     asin 一致（purchase.asin == goal.asin）→ gold_purchase
```

task 0：`raw_trace.steps[13].raw.purchase.asin`（747848614498）== `raw_trace.steps[13].raw.goal.asin`（747848614498）→ `reward_type = "gold_purchase"`。

### 第 4 步：查分数表

`DEFAULT_REWARDS` 表：`gold_purchase = 1.0` → **最终 reward = 1**。

产出字段汇总：

```
raw_trace.steps[13].raw.reward_detail.reward          = 1
raw_trace.steps[13].raw.reward_detail.reward_type     = "gold_purchase"
raw_trace.steps[13].raw.reward_detail.weighted_score  = 1.0
raw_trace.steps[13].raw.reward_detail.dimension_scores = {brand:0, core_functions:1, key_options:1, model:0}
```

> 注意：`dimension_scores` 里的 `brand=0`/`model=0` 是「不考核」（active=false），不是扣分。分数表里还有负分惩罚（max_steps=-0.5、repeat_loop=-0.65 等），但 task 0 没触发。

---

## 三、rule gate（evaluate.py 确定性评测）

**只读 `raw_trace`，零 LLM。**

### 3.1 结果层 —— 读终局步 `raw_trace.steps[13].raw.reward_detail`

| evaluate 读的完整字段 | 解释 | task 0 值 |
|---|---|---|
| `reward_detail.reward` | 最终得分 | `1` |
| `reward_detail.reward_type` | 结局类型标签 | `"gold_purchase"` |
| `reward_detail.purchase_success` | 是否买对 gold | `true` |
| `reward_detail.target_asin_match` | 买到 asin 是否 == gold asin | `true` |
| `raw.reward_valid` | reward 是否有效（unverifiable 时为 false） | `true` |
| `reward_detail.hard_gates` | 硬门槛（category/budget 的 pass/fail） | `{budget:pass, category:pass}` |
| `reward_detail.dimension_scores` | 四维软分 | `{brand:0, core_functions:1, key_options:1, model:0}` |
| `reward_detail.evidence.preference_scoring.dimensions.brand.active` | brand 维度是否考核 | `false` → 0 标「不考核」 |
| `reward_detail.evidence.preference_scoring.dimensions.core_functions.active` | core_functions 是否考核 | `true` → 1 标「正常」 |

### 3.2 过程层 —— 读非终局步 `raw_trace.steps[1..12].raw.progress.evidence_added`

`evidence_added` 是**过程证据列表**，元素是 `类型:内容` 字符串，按 `:` 前缀分类：

| 前缀 | 解释 | task 0 计数 |
|---|---|---|
| `result_set:` | 执行了一次搜索、产生新结果集 | 3 条 |
| `product:` | 打开了一个商品详情页（如 `product:747848614498`） | 2 个去重 asin |
| `constraint:` | 核验了一个硬门槛（如 `constraint:…:category:pass`） | 4 条 |
| `option:` | 选择了一个商品规格（如 `option:…:color:满天星`） | 1 条 |
| `subpage:` | 查看了详情子页（Description/Features/Reviews） | 3 条 |

另读 `raw_trace.steps[1..12].raw.progress.consecutive_repeats` = 连续重复动作次数（task 0 为 0）。

### 3.3 失败归类 + 撞对

- 归类读 `reward_detail.purchase_success` = true → 直接「成功」。
- 撞对读 `purchase_success` + `goal.goal_options`（规格要求，非空）+ `hard_gates`（有门槛）+ evidence → option/constraint 都不缺 → 不撞对。

---

## 四、rubric 拿哪些字段生产

**离线数据集（`items_eval_train.json` → `get_goals`），不读运行 trace。** 投影后喂 Flash。

| 喂 Flash 的 goal 投影字段 | 解释 | task 0 用途 |
|---|---|---|
| `goal.instruction_text` | 用户需求原文（Query） | 提炼约束的语义来源 |
| `goal.category` | 目标类目 | → 「品类=乳胶枕」 |
| `goal.attributes` / `goal.expected_core_functions` | 必须属性/核心功能 | → 「泰国进口」「天然乳胶」「护颈椎」 |
| `goal.goal_options` / `goal.required_options_by_key` | 正确答案规格（按轴拆分） | 规格核验依据（不照抄原文） |
| `goal.price_upper` | 预算上限 | → 「价格1000以下」 |
| `goal.expected_brand` / `goal.expected_model` | 品牌/型号要求（空=不考核） | 陷阱判断（空=不写品牌） |
| `goal.user_persona` | 用户画像 | 陷阱判断（品牌偏好≠需求） |

**剔除字段**（不喂 Flash）：`asin`、`name`、`instruction_simple`、`query`、`reason_key`、`feature_sources`。

task 0 的 rubric 8 条：品类/儿童/泰国进口/天然乳胶/柔软弹性(soft)/护颈/满天星/预算1000。

---

## 五、LLM as judge（judge.py）

**只读 `model_trace.json` + 冻结 rubric，不碰 raw/goal/reward。**

读的字段：

| 字段（完整路径） | 解释 | task 0 实际 |
|---|---|---|
| `model_trace.steps[].tool_name` | 动作类型（search/click） | search/click |
| `model_trace.steps[].tool_args` | 动作参数（keywords/value） | `{"value":"747848614498"}` |
| `model_trace.steps[].observation` | 模型当时看到的页面文本 | 每步页面 |
| `rubrics/0.json` 的 `rubric.selected_constraints` | 冻结的约束清单 | 8 条 |

### 角度一：逐条 rubric 判四态（面板②）

对每条 rubric，判一个状态，并引用支撑的 step：

| 状态 | 含义 |
|---|---|
| `satisfied` | 轨迹的 Observation 文本中有明确证据表明该要求被满足 |
| `violated` | 有明确证据表明该要求被违反 |
| `unknown` | 没有足够证据判断 |
| `not_applicable` | 该要求不适用于当前任务 |

task 0 预期（按轨迹证据）：
- c0001 品类 → satisfied（step2 打开商品页 + step13 买下）
- c0007 满天星 → satisfied（step12 点「【推荐4-6岁】…满天星」）
- c0005 柔软弹性 → unknown（Observation 无「柔软」证据）
- 其余（儿童/泰国/天然/护颈/预算）→ satisfied

### 角度二：五维度过程质量打分（面板③）

对整条轨迹，五个维度各打 0/1/2：

| 维度 | 含义 | 打分标准 |
|---|---|---|
| `search_strategy`（搜索策略） | 搜索词是否合理、是否收敛、是否避免无效重复搜索 | 0=明显问题/基本没完成 |
| `candidate_utilization`（候选利用） | 是否打开并利用候选商品、是否对比多个候选 | 1=部分完成，有错误或低效 |
| `evidence_verification`（证据核验） | 是否打开详情/子页核验属性、规格 | 2=整体合理无明显问题 |
| `decision_quality`（决策质量） | 购买/放弃决策是否符合需求，是否避免随意购买或错误放弃 | |
| `termination_efficiency`（终止效率） | 是否在合适时机结束，不过早放弃、不过度搜索、避免重复超步数 | |

> ⚠️ task 0 的 judge **实际输出为空**——query 含「5岁小孩/儿童」被 yowant 代理审核拒答，`eval/judgments/0.json` 不存在。上面是「读什么、怎么判」的说明，非真实跑出结果。

---

## 六、一句话

> task 0：**hard gate 用 raw_trace 终局步的 `purchase` vs `goal` 算 budget/category 都 pass；reward 接着走四维软匹配（core_functions 5/5 + key_options 1/1，brand/model 不考核）得 match_score=1.0，asin 一致 → gold_purchase=1.0；rule gate 读 raw_trace 的 `reward_detail`（结果）+ `progress.evidence_added`（过程五类指纹）确定性算出 reward=1、不撞对；rubric 离线用 goal 投影字段 + Flash 生成 8 条；LLM judge 读 model_trace 的 steps 字段，角度一判四态、角度二五维度打分，但 task 0 因审核拒答没跑出 verdict。**
