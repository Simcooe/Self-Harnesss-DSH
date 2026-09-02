# Trace 字段对照说明

本文说明 `runs/<run>/` 下三个文件里每个字段的含义，以及它们之间的关系：

- `traces/<id>.model_trace.json` — 模型可见视角（Agent 当时能看到的全部信息 + 终局摘要）
- `traces/<id>.raw_trace.json` — 环境真实返回视角（simulator 每步的完整 result，含所有证据）
- `reset/<id>.json` — reset 阶段 simulator 的完整返回

字段名全部来自真实数据与源码，未做任何重命名。

---

## 总览

| 文件 | 内容定位 | 是否 simulator 原始字段 |
|---|---|---|
| `raw_trace.json` 的 `reset` / `steps[].raw` | simulator 的 `result` **原样** | ✅ 是（`shop_agent.py` 的 return_info） |
| `reset/<id>.json` | reset 的 `result` 原样 | ✅ 是 |
| `model_trace.json` | 模型可见投影 + 终局摘要 | ❌ 是导出脚本 `export_trace.py` 重新组织的 |

关键点：`model_trace` 里每个 step 的 `observation` 等于 raw 的 `instruction` 字段，
但**终局那一步被 `shop-tools.js` 替换**成 `Episode finished.` 占位符（防止 done 页泄漏 reward 和 gold 答案）。

---

## 一、`model_trace.json`

### 顶层字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `task` | string | 用户需求原文（取自 session 第一条 user 消息） |
| `step_count` | int | 模型调用工具的总步数 |
| `steps` | array | 每步的模型可见记录 |
| `terminal` | object | 终局结果摘要（从最后一步 raw 抽子集拼出） |

### `steps[]`（每步）

| 字段 | 类型 | 含义 |
|---|---|---|
| `step` | int | 步序号，从 1 开始 |
| `tool_name` | string | 工具名：`search` / `click` / `finish` |
| `tool_args` | object | 工具入参：`search`→`{keywords}`；`click`→`{value}`；`finish`→`{reason}` |
| `observation` | string | 模型这一步之后看到的页面文本（= raw 的 `instruction`，终局那步被替换为占位符） |

### `terminal`

| 字段 | 含义 | 来源（raw） |
|---|---|---|
| `done` | 是否终局 | `raw.done` |
| `termination_reason` | 结束原因，如 `gold_purchase` | `raw.termination_reason` |
| `reward` | 最终得分（0~1） | `raw.reward` |
| `reward_valid` | reward 是否有效 | `raw.reward_valid` |
| `reward_type` | 奖励类型 | `raw.reward_detail.reward_type` |
| `purchase_success` | 是否购买成功 | `raw.reward_detail.purchase_success` |
| `purchase` | 购买信息快照 | `raw.purchase` |

### `terminal.purchase`

| 字段 | 含义 |
|---|---|
| `asin` | 买到商品的 id |
| `name` | 商品标题 |
| `category` | 类目（如 `床上用品›枕头›乳胶枕`） |
| `attributes` | 商品属性列表 |
| `options` | 选中的规格，如 `{"颜色分类": "【推荐4-6岁】塔拉蕾乳胶二阶枕：满天星"}` |
| `price` | 成交价（选规格后） |
| `instruction_text` | 原始需求 |
| `product_category` / `query` | 数据集辅助类目 / 搜索词字段（可能为空） |

---

## 二、`raw_trace.json`

### 顶层字段

| 字段 | 含义 |
|---|---|
| `task` | 同 model_trace |
| `step_count` | 同 model_trace |
| `reset` | reset 时 simulator 的完整 result |
| `steps` | 每步的完整 raw（`steps[].raw` = simulator 的 `result` 原样） |

### `reset`（`shop_agent.py` reset 返回）

| 字段 | 含义 |
|---|---|
| `env_idx` | 租到的环境槽位号 |
| `idx` | 任务 id |
| `environment_version` | 环境版本 `shopsimulator-environment-v2.1` |
| `instruction` | `"Instruction: <完整需求>"` |
| `instruction_simple` | 简化版需求 |
| `goal_options` | **正确答案规格（gold，模型不可见）** |
| `message` | `"Task 0 started"` |
| `observation_state` | 初始结构化观察（首页） |
| `user_persona` | 用户画像（仅 persona 模式，含 10 个键） |
| `reason_key` | 画像推理 key（可能为 null） |

### `steps[].raw`（每步 interact 的 result，`shop_agent.py` return_info）

| 字段 | 含义 |
|---|---|
| `done` | 是否终局 |
| `env_idx` | 槽位号 |
| `idx` | session id，如 `"slot-1-0"` |
| `instruction` | **当前页观察文本**（喂给模型的那段） |
| `message` | `"Continue interaction"` |
| `observation_state` | 结构化观察 |
| `progress` | 证据轨迹（evidence tracker） |
| `reward` | 本步 reward（非终局恒 0） |
| `reward_detail` | 终局奖励明细 |
| `purchase` | 终局购买信息（同 `terminal.purchase`） |
| `goal` | 终局 gold 完整目标 |
| `over` | 是否因超长/结束而终止 |
| `termination_reason` | 终局原因（仅 done 时） |
| `reward_valid` | reward 是否有效（仅 done 时） |

---

## 三、嵌套对象

### 3.1 `observation_state`（结构化观察，`observation.py` 构建）

按页面类型（`page_type`）不同，字段不同。

**公共字段**

| 字段 | 含义 |
|---|---|
| `observation_version` | `shopping-observation-v2` |
| `page_type` | `search_home` / `search_results` / `product_detail` / `information_subpage` / `terminal` |
| `search_available` | 本页有无搜索框 |
| `actions` | 可点击按钮列表（同模型看到的"可点击的按钮"） |

**搜索结果页（`search_results`）**

| 字段 | 含义 |
|---|---|
| `query` | 原始搜索词 |
| `normalized_query` | 归一化查询 |
| `page` | 当前页码 |
| `total_pages` | 总页数 |
| `total_results` | 总结果数 |
| `rank_start` / `rank_end` | 本页商品排名区间 |
| `products[]` | 每件商品的结构化信息 |

**`products[]` 每项**

| 字段 | 含义 |
|---|---|
| `asin` | 商品 id |
| `title` | 标题 |
| `brand` | 品牌（= 店铺名） |
| `category` | 类目 |
| `price` | 价格（单值或区间，如 `"428.0 to 1096.0"`） |
| `key_attributes` | 关键属性列表 |
| `rank` | 排名 |

**商品详情页（`product_detail`）额外字段**

| 字段 | 含义 |
|---|---|
| `product` | 当前商品 `{asin,title,brand,category,price,key_attributes}` |
| `selected_options` | 已选规格 |
| `available_options` | 所有可选规格 |
| `selected_price` | 选规格后的价格 |

**信息子页（`information_subpage`）额外字段**

| 字段 | 含义 |
|---|---|
| `subpage` | `Description` / `Features` / `Reviews` / `Attributes` |
| `content` | 该子页正文 |

### 3.2 `progress`（EvidenceProgressTracker 轨迹）

| 字段 | 含义 |
|---|---|
| `termination_version` | `shopping-termination-v3` |
| `action_signature` | 本步动作签名（`{"action":"search","argument":"..."}`） |
| `step_count` | 累计步数 |
| `consecutive_repeats` | 连续重复动作次数 |
| `no_progress_steps` | 连续无进展步数 |
| `new_asin_count` | 本步新出现 ASIN 数 |
| `seen_asin_count` | 累计见过 ASIN 数 |
| `opened_candidate_count` | 累计打开候选商品数 |
| `effective_result_sets` | 有效搜索结果集数 |
| `evidence_added` | 本步新增证据（`result_set:hash`、`product:asin`、`constraint:asin:category:pass`、`subpage:asin:description`） |
| `credited_evidence_added` | 计入的有效证据 |
| `runtime_progress_added` | 运行时新增证据 |
| `evidence_counts` | 分类计数 `{constraint, option, product, result_set, subpage}` |
| `runtime_evidence_counts` | 同上（运行时） |
| `termination_reason` | 触发终止时的原因，否则 null |

### 3.3 `reward_detail`（终局奖励明细）

| 字段 | 含义 |
|---|---|
| `reward` | 最终 reward |
| `reward_version` | reward 版本 |
| `reward_type` | 类型，如 `gold_purchase` |
| `purchase_success` | 是否买到 gold |
| `target_asin_match` | 是否命中目标 ASIN |
| `reward_valid` | 是否有效 |
| `sampling_invalid` | 是否采样无效 |
| `termination_reason` | 结束原因 |
| `dimension_scores` | 各维度得分 `{brand, core_functions, key_options, model}` |
| `weighted_score` | 加权总分 |
| `terminal_utility` | 终局效用 |
| `evidence_coverage` | 证据覆盖率 |
| `hard_gates` | 硬性门槛（类目/预算等）是否通过 |
| `evidence` | 详细证据 |

**`evidence` 子字段**

| 字段 | 含义 |
|---|---|
| `comparator_version` / `reward_feature_version` / `variant_price_version` | 各子模块版本号 |
| `expected_reward_feature_version` | 期望的 reward 特征版本 |
| `preference_scoring` | 偏好打分详情（每维度 active / coverage / passed_count / required_count / results[]） |
| `price_resolution` | 规格 → 价格解析结果 |

### 3.4 `goal`（终局 gold 完整目标）

| 字段 | 含义 |
|---|---|
| `asin` | 目标商品 id |
| `name` | 目标商品标题 |
| `category` | 类目 |
| `attributes` | gold 属性 |
| `expected_core_functions` | 期望核心功能（= attributes） |
| `expected_brand` / `expected_model` | 期望品牌 / 型号（可能为空） |
| `goal_options` | 正确答案规格 |
| `instruction_text` / `instruction_simple` | 完整需求 / 简化需求 |
| `price_upper` | 预算上限 |
| `query` / `product_category` | 辅助字段 |
| `user_persona` / `reason_key` | 用户画像 / 推理 key |
| `required_options_by_key` / `unresolved_option_requirements` | 规格要求（按轴拆分） |
| `feature_sources` | 各特征来源（如 `core_functions: "instruction.attributes"`） |
| `option_axis_version` / `reward_feature_version` | 版本号 |
| `weight` | 采样权重 |

---

## 四、速查记忆

- **模型看到的**：只有 `model_trace.steps[].observation`（= raw 的 `instruction` 字段）。
- **环境真实返回的**：`raw_trace.steps[].raw` 全部字段 + `raw_trace.reset`。
- **评测证据**：`observation_state`（结构化商品）、`progress`（证据轨迹）、`reward_detail`（打分明细）、`goal`（gold 答案）——全部只在 raw 里，模型拿不到。
- **字段名来源**：
  - interact/reset 返回字段 → `environments/ShopSimulator/shop_env/shop_env/shop_agent.py`
  - `observation_state` 结构 → `environments/ShopSimulator/shop_env/web_agent_site/engine/observation.py`
  - `model_trace` 的组织与 `terminal` 抽取 → `scripts/export_trace.py`
