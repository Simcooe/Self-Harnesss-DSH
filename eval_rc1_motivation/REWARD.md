# Reward 的计算方式（ShopSimulator 环境原生）

> 本文说明环境如何给一个 episode 打最终分（reward）。算法在
> `environments/ShopSimulator/shop_env/web_agent_site/engine/reward.py`，**环境自带，非评测脚本实现**。

---

## 0. 一句话

reward = **两个硬门槛先卡（类目/预算）→ 四个软维度匹配 → 按「终局类型」查一张固定分数表**。
负分是惩罚，0~1 是匹配分，两者语义不同。

---

## 1. 分数查表（`DEFAULT_REWARDS`，reward.py:39-51）

「什么结局给什么分」的固定表：

| reward_type | 分数 | 含义 |
|---|---|---|
| `gold_purchase` | **1.0** | 买对 gold |
| `valid_alternative_purchase` | **0.55** | 买对等价的替代品 |
| `partial_alternative_purchase` | **-0.30 + 0.55×match_score**（封顶 0.25） | 买了部分匹配的替代品 |
| `graceful_stop` | -0.15 | 优雅放弃（搜够、确定无合适才停） |
| `early_abstain` | -0.35 | 过早放弃 |
| `max_steps` | **-0.50** | 超步数 |
| `repeat_loop` | **-0.65** | 死循环 |
| `wrong_purchase` | -0.85 | 买错（硬门槛没过） |
| `reward_unverifiable` | 0.0 | 无法核验 |

---

## 2. 环境判断「结局」看什么

不是某个单一字段，而是三个动作信号之一，各自走一条路径：

| 路径 | 触发动作 | 看什么字段 | 产物 |
|---|---|---|---|
| 购买 | 点 `Buy Now` | `purchase.asin` vs `goal` + `hard_gates` + 四维 | gold/alternative/partial/wrong |
| 放弃 | 调 `finish` | `effective_result_sets` + `opened_candidates` + `known_valid` | graceful_stop/early_abstain |
| 强制终止 | 环境检测 | `termination_reason`（repeat_loop/max_steps） | 查表负分 |

三条路径最终都落到 `reward_detail.reward_type` 这一个字段上——`reward_type` 就是「结局」的最终标签。

---

## 3. 路径一：购买（`evaluate_purchase`，reward.py:353-422）

### 3.1 两个硬门槛（hard_gates）

| 门槛 | 比较内容 | 来源 |
|---|---|---|
| `category` | 买到的类目 vs `goal.category` | `compare_category(goal.category, product)` |
| `budget` | 成交价 vs `goal.price_upper` | `_price_gate`（`price ≤ price_upper`） |

门槛结果三种状态：`PASS`（过）/ `FAIL`（不过）/ `UNVERIFIABLE`（无法核验，如区间价无法确认是否超预算）。

### 3.2 四个软维度（preference scoring）

| 维度 | 要求来源 | 权重 |
|---|---|---|
| `brand` | `goal.expected_brand` | 0.35 |
| `model` | `goal.expected_model` | 0.25 |
| `core_functions` | `goal.expected_core_functions` | 0.25 |
| `key_options` | `goal.required_options_by_key` / `goal_options` | 0.15 |

关键：
- 只有 goal 里**声明了要求**的维度才 `active`（参与计分）。`expected_brand=[]` → brand 维度 `active=false`，不参与。
- 每维 `score = 通过数 / 要求数`。
- `match_score = 各 active 维度的加权平均`（权重如上）。

### 3.3 给分优先级（reward.py:383-405）

```
1. 硬门槛有 UNVERIFIABLE → reward_unverifiable（0.0，无效）
2. 硬门槛有 FAIL       → wrong_purchase（-0.85）
3. 四维全满足(all_satisfied) →
     asin 一致 → gold_purchase（1.0）
     asin 不同 → valid_alternative_purchase（0.55）
4. 否则 → partial_alternative_purchase
        = min(0.25, -0.30 + 0.55 × match_score)
```

---

## 4. 路径二：主动放弃（`evaluate_abstain`，reward.py:462-496）

触发：Agent 调 `finish[no_suitable_product]`。

三个累计量（阈值）：

| 字段 | 含义 | 阈值 |
|---|---|---|
| `effective_result_sets` | 有效搜索次数 | ≥2 |
| `opened_candidates` | 打开过几个候选商品 | ≥2 |
| `known_valid_asins` | 已知可接受的商品数 | ==0 |

三者都满足 → `graceful_stop`（-0.15）；否则 → `early_abstain`（-0.35）。

---

## 5. 路径三：强制终止（`fixed_termination`，reward.py:499-515）

触发：环境内部 `EvidenceProgressTracker` 检测到循环或超步数，主动终止。

| 结局 | reward | 依据 |
|---|---|---|
| `repeat_loop` | -0.65 | 重复动作 / 无进展步数达阈值 |
| `max_steps` | -0.50 | 步数超 `max_steps` |

原因字符串就是 `termination_reason`，直接查表给分。

---

## 6. 用 6 条实际 trace 验证

| 任务 | reward | 计算来源 |
|---|---|---|
| 1617/0 乳胶枕 | 1.0 | 硬门槛过 + 四维全满足 + asin 一致 → gold_purchase |
| 1339/0 乳胶枕 | 1.0 | 同上 |
| 1339/1 投光灯 | 0.04375 | partial：-0.30 + 0.55×0.625 = 0.04375（key_options 选错规格拖低 match_score） |
| 1617/1 投光灯 | -0.5 | fixed_termination("max_steps") 查表 |
| 1617/2 过滤网 | -0.65 | fixed_termination("repeat_loop") 查表 |
| 1617/3 写字椅 | -0.65 | 同上 |

---

## 7. 关键结论

1. **环境给的是「终局 reward」，不是「过程 reward」**：只在 episode 结束瞬间算一次分，中间每步的 `progress` 只记录证据、不产生分数。

2. **环境 reward 是「结果导向」，不看过程**：reward 完全由终局状态决定，中间过程有没有真核验不影响 reward。因此：
   - 轨迹 A（认真核验后买对）和轨迹 B（瞎撞买对）的 reward 都是 1.0，环境分不出。
   - 区分「撞对」和「真会买」必须**额外**用 `progress`（过程证据）做一层评测——这就是 `eval/evaluate.py` 的撞对检测 + 后续 Judge 要做的「过程质量」评估。

3. **负分是惩罚，不是部分分**：`-0.5`/`-0.65` 是惩罚项；`0.04375` 是「买了替代品的部分分」。两者语义不同，混在一个均值里会失真。

4. **`weighted_score`（= match_score）是「软匹配分」，不是最终 reward**：它只反映四维匹配得多好，最终 reward 还要经过硬门槛 + 查表。

5. **`dimension_scores` 里的 0 有两义**：`active=false` 的 0 是「不考核」，`active=true` 的 0 才是「真失败」。
