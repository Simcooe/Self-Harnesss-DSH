# 评测 Pipeline：rubric、Judge 与泄题风险

> 本文是 `EVAL_LAYERS.md` 的后续。前者讲「三层分别是什么」，本文讲「三层怎么用起来做评测」。
> 例子沿用同一个任务：「帮我推荐一款适合5岁左右小孩的乳胶枕头…预算1000元以下」。

---

## 0. 与三层的关系

`EVAL_LAYERS.md` 定义了：

- **结果层** = `reward_detail`（终局步）
- **过程层** = `progress`（非终局步）
- **答案层** = `goal`（终局步）+ `reset`

本文回答：评测跑起来时，这三层各自喂给谁、怎么用、哪些绝对不能进 Judge。

---

## 1. 任务执行完的评估流程（两段式）

不是「拿 rubric 和 progress 对比」，而是两段式：

```
任务执行完
   │
   ├─ 第一段：rule gate（代码，不涉及 rubric）
   │     用 progress 统计 + 轨迹，筛掉坏轨迹：
   │     工具调用能否解析 / Action Guard 拒绝 / 点不存在的商品 /
   │     重复动作 / 是否正常结束 / 上下文是否截断
   │
   └─ 第二段：LLM Judge（拿 rubric + 完整轨迹，真正判命中）
         逐条 rubric 判四态：satisfied / violated / unknown / not_applicable
         每个判断引用真实 Event ID
```

关键：**rule gate 才用到 `progress`；Judge 的命中判断用的是「轨迹」，不是 `progress`。**

---

## 2. 轨迹是哪个字段

轨迹 = **`model_trace.json` 的 `steps` 数组**。

```
model_trace.steps[]   →  每一步一条记录
  ├─ step         步序号
  ├─ tool_name    动作（search / click / finish）
  ├─ tool_args    动作参数（keywords / value / reason）
  └─ observation  这一步之后模型看到的页面文本
```

这就是文章说的「Agent 执行的完整轨迹」—— 模型**当时实际看到和做出的**每一步序列。

| 视角 | 字段路径 | 内容 | 谁看 |
|---|---|---|---|
| **轨迹**（Judge 用） | `model_trace.steps[]` | 动作 + observation 文本 | Judge |
| 原生返回 | `raw_trace.steps[].raw` | 完整 result（含 observation_state / progress / reward_detail / goal） | 评测代码 |

Judge 只拿前者。后者里的 `goal`/`reward_detail`/`progress` 是答案和判分信号，喂给 Judge 会泄题。

---

## 3. Judge 看轨迹，不看 progress

以 rubric 里「带有满天星图案」为例，Judge 怎么判 satisfied：

- 它看轨迹：step 11 打开商品 747848614498 → 页面显示选项「【推荐4-6岁】塔拉蕾乳胶二阶枕：满天星」→ step 12 点击该选项 → step 13 buy now。
- 结论：`satisfied`，引用 step 12 的 Event ID。

它不是去 `progress.evidence_added` 里 grep `option:` 字段。`option:` 证据是环境侧自动记的，Judge 靠「读轨迹」判断。

`progress` 的用处在**确定性指标面板**和**防撞对**，不在 Judge 的逐条命中里：
- 结果层满分 + `progress` 缺「本题必需」的证据 → 代码判「撞对」，降权。
- `progress.evidence_counts` 汇总成「证据核验覆盖率」，进第四个面板。

---

## 3a. 过程证据的五类字段（`evidence_added` 详解）

`progress.evidence_added` 里环境自动记的是**过程证据字符串**，格式 `类型:内容`。共五类：

| 证据 | 格式 | 含义 | 0 号任务真实例子 |
|---|---|---|---|
| `result_set` | `result_set:<hash>` | 执行了一次搜索，产生一个新结果集 | `result_set:3e13…` |
| `product` | `product:<asin>` | 打开了一个商品详情页 | `product:747848614498` |
| `constraint` | `constraint:<asin>:<门槛>:<结果>` | 核验了一个硬门槛，结果 pass / fail / unverifiable | `constraint:747848614498:category:pass` |
| `option` | `option:<asin>:<轴>:<规格值>` | 选择了一个商品规格 | `option:747848614498:color:【推荐4-6岁】…满天星` |
| `subpage` | `subpage:<asin>:<子页名>` | 查看了详情子页（Description/Features/Reviews/Attributes） | `subpage:747848614498:description` |

逐一含义：

- **`result_set`**：搜了一次，环境记一个结果集哈希。重复搜同一个词不会新增（算 repeat）。
- **`product`**：点开某个 ASIN 进商品页。最基础的动作证据——不打开商品页就不可能买。
- **`constraint`**：环境对硬门槛做的自动核验。`<门槛>` 常见 `category`（类目）、`budget`（预算）。`<结果>` 三种：
  - `pass` = 过了
  - `fail` = 没过
  - `unverifiable` = 没法核验（如 PARATEX 区间价 738~1533，无法确认是否 ≤1000 → step8 记 `budget:unverifiable`）
- **`option`**：在商品页选了规格（颜色/尺寸/容量等）。规则要求「买前至少选一个规格」，有 `option:` = 不是瞎点 Buy Now。
- **`subpage`**：点开子页看详情。这是「看得多仔细」的信号，不是必需的。

五类证据组合起来，就是 Agent 动作轨迹在环境侧的客观指纹，用来判断「是不是真在认真购物、核验过约束」，而不是「碰运气买对」。

### 3b. 撞对检测是 goal 感知的，不是固定清单

**「该有哪些证据」由任务本身的 goal 决定，不是写死一张清单。** 四类动作证据的必需性不同：

| 证据 | 何时必需 | 何时天然没有 |
|---|---|---|
| `product:` | **购买必需要有**（不打开商品页到不了 Buy Now） | 不存在「没有」——打开是购买的前提 |
| `option:` | **仅当 gold 商品有规格时必需**（`goal_options` 非空） | 商品无 `customization_options` / `goal_options` 为空 → 直接买合法，无 `option:` 是正常的 |
| `constraint:` | **仅当 goal 有可核验硬门槛时必需**（`category` 恒有；`budget` 只有 Query 说了预算才有） | 没提预算 → 无 budget 门槛；门槛不可核验时记 `unverifiable` 而非 `pass` |
| `subpage:` | **永远不是硬必需**（只是「看得多细」的信号） | 有些商品 description/features/reviews 本身就是空的 |

正确的撞对检测应「先读 goal 推导本题要求哪些证据」：

```
product    → 恒必需（针对最终买到的那个 asin）
option     → goal.goal_options 非空 才必需
constraint → category 恒必需；budget 需 price_upper 存在
subpage    → 永不作为硬必需

撞对 = purchase_success=true 但缺少「本题必需的那几类证据」
```

例子对比：

- **0 号任务**（乳胶枕，有规格）：`goal_options` 非空 → `option:` 必需。轨迹有 `option:` → 不判撞对 ✅
- **假设「299 元白色空气炸锅」任务**，gold 无颜色/容量可选（`customization_options` 空）：`goal_options` 空 → 不要求 `option:`。模型搜→开→核验类目预算→买，证据只有 `product:` + `constraint:` → **不能**因为它没有 `option:` 判撞对，因为本题根本没有规格可选。

这与 rubric 的关系：**撞对检测是 rubric 的「确定性投影」**——rubric 从 goal 提炼 Hard 要求，撞对检测就是查「这些 Hard 要求对应的证据有没有出现」。

---

## 4. rubric 的形成（从乳胶枕例子出发）

原料两样：**Query（用户明说）+ goal（TaskFacts，标准答案）**。

**原料**
- Query（`goal.instruction_text`）：「…泰国生产的进口款，天然乳胶材质…满天星图案…预算1000元以下。」
- TaskFacts（`goal`）：`category`=乳胶枕、`attributes`=[泰国,进口,天然,护颈椎,枕芯]、`goal_options`=[满天星规格]、`price_upper`=1000、`expected_brand`=[]、`expected_model`=[]、`user_persona` 品牌偏好=梦洁宝贝高。

**第一步：从 Query 逐句拆「用户明说的要求」**

| Query 片段 | 候选约束 |
|---|---|
| 乳胶枕头 | 品类=乳胶枕 |
| 适合5岁左右小孩 | 适用儿童 |
| 泰国生产的进口款 | 产地泰国、进口 |
| 天然乳胶材质 | 材质=天然乳胶 |
| 摸起来更软弹性更好 | 柔软、弹性好 |
| 能保护颈部脊柱 | 功能=护颈椎 |
| 带有满天星图案 | 图案=满天星 |
| 预算1000元以下 | 价格≤1000 |

**第二步：去 goal 核验哪些是真硬约束**

| Query 要求 | goal 证据 | 可验证 |
|---|---|---|
| 品类=乳胶枕 | `category` | ✅ hard |
| 产地泰国进口 | `attributes` 含 泰国/进口 | ✅ hard |
| 材质天然乳胶 | `attributes` 含 天然 | ✅ hard |
| 护颈椎 | `attributes` 含 护颈椎 | ✅ hard |
| 满天星 | `goal_options` 含 满天星 | ✅ hard |
| 预算≤1000 | `price_upper`=1000 | ✅ hard |
| 柔软弹性好 | goal 无对应字段 | ⚠️ soft |

**第三步：识别 TaskFacts 里的陷阱并剔除**

TaskFacts 有两样 Query 没提的东西：
1. `user_persona` 品牌偏好「梦洁宝贝=高」
2. `goal.expected_brand=[]`、`expected_model=[]`（空）

结论：品牌、型号**不是本题要求**。证据就是 `expected_brand=[]`——gold 商品自己都没声明品牌是硬约束。把这两条从 rubric 删掉（或标 not_applicable），否则 Judge 会因为「没核验品牌」假扣分。

**第四步：分 Hard / Soft**
- Hard（明说、可验证、必须满足）：品类、产地、材质、护颈椎、满天星、预算、适用人群
- Soft（说了但主观、无法严格验证）：柔软、弹性好

**第五步：组装成 rubric（文章格式）**

```json
{
  "selected_constraints": [
    {
      "candidate_id": "c0001",
      "description": "商品品类为乳胶枕",
      "hardness": "hard",
      "query_quote": "乳胶枕头",
      "selection_reason": "Query 明确提出了商品类型"
    },
    {
      "candidate_id": "c0006",
      "description": "价格在1000元以内",
      "hardness": "hard",
      "query_quote": "预算在1000元以下",
      "selection_reason": "Query 明确提出了预算上限"
    },
    {
      "candidate_id": "c0008",
      "description": "柔软、弹性好",
      "hardness": "soft",
      "query_quote": "摸起来更软、弹性更好",
      "selection_reason": "主观感受，无法用硬字段严格验证"
    }
  ]
}
```

注意：rubric 里写自然语言规则（「带有满天星图案」），**不是** `goal_options` 原文「【推荐4-6岁】塔拉蕾乳胶二阶枕：满天星」——原文给 Judge 等于泄题。

**第六步：冻结 + 喂 Judge**
生成一次后冻结存盘，所有模型共用同一份。Judge 拿到：Query + 冻结 rubric + 完整轨迹 + Action Guard 拒绝信息。

---

## 5. 候选约束是写死的吗

不是。**候选约束是第一个 LLM（DeepSeek V4 Flash）生成的，不是代码写死的规则。**

Flash 负责：选哪些候选、去重、整理成自然语言、判断 hard/soft、提供 Query 原文依据（`query_quote`）。

为什么不能写死：Query 是自由文本，每个任务措辞不同（「预算1000以下」/「300元左右」/「别太贵」），且有些要求是隐含的、有些是陷阱，只能靠模型理解。

区分两个阶段：

| 阶段 | 是否固定 |
|---|---|
| 生成 rubric | 不固定，Flash 用 LLM 生成 |
| 冻结 rubric | 固定，生成一次后存盘不再改，所有模型共用 |

「冻结」才是写死，但冻结的是**生成结果**，不是生成规则。

工程化折中（可选）：你仓库里 `goal` 有结构化字段（`category`/`attributes`/`goal_options`/`price_upper`），可做混合版——硬约束直接读字段（零误差、省钱），LLM 只补 soft + 转自然语言 + 判陷阱。

---

## 6. 为什么喂 Judge 会泄题

Judge 拿到标准答案后会产生两种系统性偏差。

**偏差一：把「没要求」当成「没做到」（假扣分）**

`goal` 里有 Query 没提的信息：
```json
"user_persona": { "品牌偏好": [{"品牌名称":"梦洁宝贝","偏好程度":"高"}] }
```
Query 一个字没提品牌。但 Judge 看到「品牌偏好：梦洁宝贝=高」，可能默认「买梦洁宝贝」是要求，于是 Agent 买了完全符合 Query 的杂牌乳胶枕，被判 violated → 假扣分。

**偏差二：把「瞎撞对」当成「做得好」（假加分）**

Judge 看到 `goal.asin`（=747848614498）。Agent 第一条搜索碰巧搜出 asin，没看详情、没核验、没选规格直接 buy now，结果 `purchase.asin == goal.asin`。Judge 看到「目标 ASIN」和「买到 ASIN」一致，很可能直接判「这条轨迹很好」——但 Agent 全程没核验过，换任务就抓瞎。

**同理 reward / reward_detail 也泄**
- `dimension_scores` 直接告诉 Judge 哪几维满分 → Judge 等于抄答案
- `observation_state` 里有 brand/key_attributes/selected_price 等模型当时没看到的隐藏字段

一句话：**泄题 = Judge 获得了模型决策时不可能拥有的信息，导致它要么拿没提的要求乱扣分，要么拿答案比对假装认真，测出的不是 Agent 真实能力，而是 Judge 知不知道答案。**

---

## 7. goal 只在终局步有，为什么还有泄题风险

分清两个完全不同的「泄题对象」：

| 对象 | 时间 | goal 可见吗 | 风险 |
|---|---|---|---|
| **模型（Agent）** | 执行中 | ❌ 看不到 | 无风险，`shop-tools.js` 已隔离 |
| **Judge（评测者）** | 执行结束后 | ✅ 看得到 | **这才是泄题风险** |

「之前 step 不暴露」指的是**模型执行过程中** goal 没泄露——这确实无风险。

但 Judge 是**事后离线**跑的评测进程。评测时 episode 已跑完，它拿到的是完整数据——包括最后一步的 `raw.goal`（还有 reset 里的 `goal_options`/`user_persona`）。

所以问题变成：**你喂给 Judge 的是哪个文件**
- 喂 `model_trace.steps`（只有动作 + observation 文本）→ 不泄题，里面没有 goal
- 喂 `raw_trace.steps[].raw`（含终局步的 `goal`）→ 泄题

文章原话印证：Judge「看不到 Reward、Gold ASIN、隐藏商品字段和原始未投影 Observation」——这里的 Gold ASIN 就是 `goal.asin`，作者明确把它挡在 Judge 外面。

---

## 8. 正确做法总结

| 数据 | 给谁 | 用途 |
|---|---|---|
| `model_trace.steps`（轨迹） | Judge | 逐条 rubric 判四态 + 引用 Event ID |
| Query + 冻结 rubric | Judge | 评分标准（只反映用户要求，不照抄答案） |
| `goal` / `reward_detail` / `progress` / `observation_state` | 评测代码 | 结果统计、防撞对、rubric 生成 |

核心原则：**Judge 只拿「模型当时看到的东西」+「反映用户要求的 rubric」；答案和判分信号一律藏在 `raw` 里，只给代码，不进 Judge。**
