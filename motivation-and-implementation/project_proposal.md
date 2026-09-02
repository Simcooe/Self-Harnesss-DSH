# ShopHorizon 项目定题方案

## 1. 最终定题

**中文题目：** 面向个性化多轮购物 Agent 的可审计状态转换与交易安全 Harness

**英文题目：** ShopHorizon: Audited State Transitions and Transaction-Safe Execution for Long-Horizon Personalized Shopping Agents

**主要场景：** ShopSimulator 的 **Multi-Turn + Personalization** 场景。

**一句话定义：**

在不训练模型、不读取隐藏目标和 rubric 的前提下，通过 Harness 强制执行“管理、执行、审计、提交”协议：模型只能提出动作和状态更新建议，只有从 Shopper 或 ShopEnv 独立验证的事实才能进入跨轮持久状态，最终购买还必须通过绑定需求、商品、规格和价格版本的交易检查。

本项目不以“让模型变得更聪明”为目标，而是研究：

> 当购物任务变长、需求不断补充、候选商品不断切换时，如何用可审计状态转换代替模型的自我记忆和自我判定，保证 Agent 始终围绕正确需求、正确商品和正确规格做决策？

ShopSimulator 的场景难点与典型失败模式参见 [ShopSimulator 场景难点分析](./shop_simulator_challenges.md)。

---

## 2. 研究动机

长程购物中的很多失败并非单纯来自模型缺少商品知识，而是来自运行过程中的状态失控。

### 2.1 需求状态失控

用户画像记录用户长期偏爱黑色，但用户在本轮对话中明确要求白色。Agent 在后续搜索和比较中遗忘本轮要求，再次按照画像选择黑色。

### 2.2 商品证据失控

商品 A 满足防水要求，商品 B 满足预算要求。Agent 在多轮浏览后将两个商品的证据混合，错误地认为商品 B 同时满足防水和预算。

### 2.3 购买状态失控

用户确认的是商品 A 的蓝色、40 码版本，但 Agent 在确认后切换了颜色或尺码，最终购买了用户没有确认过的规格。

因此，本项目将三类持续变化的状态外置到 Harness，并要求每次跨轮状态更新都有独立证据：

```text
Intent State       用户现在到底需要什么
Candidate State    每个商品和规格分别满足什么
Transaction State  用户究竟确认购买了什么
```

这些状态不是由模型自由总结后直接写入。模型输出只能形成 untrusted proposal；Harness 必须通过独立 Auditor 检查 Shopper 原始回复、ShopEnv 结构化 observation 或只读环境状态，随后由确定性 Committer/Reducer 更新 authoritative state。

核心假设是：

> 在模型、任务、工具和任务级执行预算保持一致的情况下，可审计状态转换、受限 fresh-context 执行和工具层交易约束，比单纯增加购物提示词更能提高长程购物成功率，并显著降低状态错位导致的购买错误。

---

## 3. 项目范围

### 3.1 主场景

项目只将第四种场景 **Multi-Turn + Personalization** 作为主要研究对象。该场景同时包含：

- 初始需求不完整，需要 Agent 主动询问；
- 用户画像提供长期偏好和历史信息；
- 当前需求可能覆盖或冲突于用户画像；
- Agent 需要进行多轮搜索、浏览、比较和规格选择；
- 用户可能拒绝推荐、补充需求或者修改要求；
- 最终购买需要绑定正确商品、正确规格和有效确认。

其他三种场景只进行小规模回归测试，用于检查 Harness 是否破坏简单任务，不承担项目的主要研究结论。

### 3.2 明确不做的内容

为保证项目边界清晰，第一阶段不做：

- Harness 自进化；
- RL、SFT 或其他模型训练；
- 四种场景平均投入；
- 通用浏览器 Agent；
- 开放式多 Agent 协作或并行 swarm；项目只保留固定、串行、权限隔离的 Manager、Executor、Auditor 角色；
- 自动修改 prompt；
- 使用 LLM Judge 替代所有确定性指标；
- 与研究贡献无关的复杂前端。

这些方向可以作为后续扩展，但不能影响核心系统和主实验的完成。

---

## 4. 信息边界与防泄漏设计

ShopSimulator 的隐藏目标和 rubric 必须与 Agent 运行时严格隔离：

```text
                    +-- 用户画像 ----------------> Shopping Agent
任务数据 -----------+-- 隐藏完整目标 ------------> Shopper Simulator
                    +-- 隐藏目标 + Rubric --------> Offline Evaluator
```

Shopping Agent 和 Harness 只能访问：

- 用户画像；
- 初始模糊需求；
- Shopper 已经在对话中透露的信息；
- 公开商品页面；
- 工具执行结果；
- Harness 从上述公开信息归约得到的状态。

Shopping Agent 和 Harness 不能访问：

- 隐藏完整目标；
- target product；
- rubric；
- reward detail；
- evaluator 判断；
- 环境内部未公开的商品标签。

需要通过接口隔离、日志字段审计和自动化测试共同保证这一边界。Rubric 只属于离线评测，不是 Harness 的运行时知识。

---

## 5. 系统架构

```text
                 +-----------------------------------+
                 | Authoritative Audited Shopping   |
                 | State + prior audit references   |
                 +----------------+------------------+
                                  |
                                  v
                 Manager（无 ShopEnv 修改权限）
                 生成 bounded subtask contract
                                  |
                                  v
                 Fresh-context Executor
                 获得本轮状态、合同、工具和预算
                        |                    |
            ask_shopper |                    | search / click
                        v                    v
              Shopper Simulator       ShopSimulator ShopEnv
                        |                    |
                        +---------+----------+
                                  |
                     immutable public observations
                                  |
                                  v
                 Fresh-context Shopping Auditor
                 仅使用只读 observation/evidence API
                                  |
                           clean audit report
                                  |
                                  v
                 Deterministic State Committer
                                  |
                                  +--------> 下一轮 Manager

Purchase path:
Executor -> prepare_purchase -> Purchase Guard
         -> Shopper confirmation -> commit_purchase
         -> Guard recheck -> ShopEnv Buy Now
```

Manager、Executor 和 Auditor 可以使用同一个 backbone model，但必须是相互隔离的调用。模型负责：

- Manager 根据 audited state 选择一个当前子任务及其验收条件；
- Executor 理解用户表达并完成本轮询问、搜索、浏览或推荐；
- Auditor 对自然语言需求和商品证据进行独立语义核验；
- Executor 生成对用户的自然语言回复和 action proposal。

Harness 负责：

- 启动相互隔离、预算受限的角色上下文；
- 控制每个角色能够看到的上下文和能够调用的工具；
- 保存 authoritative audited state；
- 追踪每条需求的来源和版本；
- 将商品证据绑定到具体商品和规格；
- 阻止 Executor 的自我声明直接改变持久状态；
- 阻止 Auditor 修改被检查的购物状态；
- 在购买前执行强制一致性检查；
- 支持任务恢复、重放和审计。

Prompt 只用于定义各角色的语义任务，角色隔离、fresh context、工具权限、状态提交和购买拦截必须由程序强制执行。

---

## 6. 核心模块

### 6.1 Shopper Simulator Adapter

当前工程首先需要补齐真正的 Multi-Turn + Personalization 交互闭环。

新增模型可见工具：

```text
ask_shopper(question)
```

执行流程：

```text
Agent 提问
-> Harness 调用 Shopper Simulator
-> Shopper 根据隐藏目标自动回答
-> 原始回答作为不可变环境事件写入日志
-> Manager/Extractor 提出 Intent 更新 proposal
-> Auditor 对照原始回答独立核验
-> Committer 更新 audited Intent records
-> 下一轮 Executor 获得新的状态投影
```

Shopper Simulator 需要支持：

- 从模糊需求开始对话；
- 只在被询问时逐步透露隐藏需求；
- 对不满足要求的推荐进行拒绝；
- 补充或修改需求；
- 在购买前进行最终确认；
- 在个性化模式下与用户画像共同构成任务条件。

正式 benchmark 使用自动 Shopper Simulator，真人回答只作为可选 Demo 模式。

Shopper Simulator 是补齐 benchmark 的环境基础设施，不作为本项目的核心方法创新。

### 6.2 Shopping MEA Control Loop

ShopHorizon 将长程购物执行组织为连续的 **Manage-Execute-Audit-Commit** 轮次，而不是让一个不断增长的 Agent session 同时负责执行、记忆和判断自己是否完成。

#### Manager

Manager 读取原始公开任务、当前 authoritative state 和历史 audit references，但没有 `ask_shopper/search/click/Buy Now` 权限。它只能：

- 选择一个尚未完成的购物子目标；
- 检查依赖和前置条件；
- 生成 bounded subtask contract；
- 决定下一步是 `execute`、`done`、`blocked` 还是 `ask`。

典型 subtask contract 包括：

```text
goal: 确认用户的硬预算上限
acceptance: 获得一条来自当前 Shopper 回复的明确预算事实
allowed_tools: ask_shopper
relevant_state: active intents + unresolved budget
budget: 1 次 Shopper 询问
```

#### Executor

Executor 是唯一可以发起 Shopper 交互或改变 ShopEnv 页面/选择状态的角色。每轮启动一个新的、预算受限的上下文，只接收：

- 原始任务的公开部分；
- 当前 audited state 的必要投影；
- 本轮 subtask contract；
- 合同引用的少量 prior audit evidence；
- 本轮允许使用的工具。

Executor 不接收之前各轮的完整推理和原始交互轨迹。它返回的执行报告只描述“做了什么”，不能证明“已经完成”。

#### Shopping Auditor

Auditor 在独立 fresh context 中运行，不接收 Executor 的内部推理，也不能调用会改变任务状态的工具。它通过以下只读来源核验结果：

- Shopper 的原始回复事件；
- ShopEnv 返回的结构化 public observation；
- 当前页面、已选规格和价格的只读快照；
- 带 product/variant/page/step provenance 的证据记录。

Auditor 输出：

```text
completion: complete | incomplete | blocked
integrity: clean | suspect | violation
verified_facts: [...]
rejected_claims: [...]
remaining_gaps: [...]
evidence_refs: [...]
```

只有 `integrity=clean` 且拥有环境证据的事实才有资格进入持久状态。

#### State Committer

Committer 是确定性代码，不调用模型。它校验 audit report schema、证据引用和 revision，然后生成 committed state events。Manager、Executor 和 Auditor 都不能绕过 Committer 直接修改 authoritative state。

长程任务因此被表示为：

```text
S_i
-> Manager contract C_i
-> fresh Executor changes E_i
-> read-only Auditor report V_i
-> deterministic commit
-> S_(i+1)
```

### 6.3 Audited Event-Sourced State Engine

Harness 不依赖模型自由总结保存关键状态。日志明确区分环境事实、模型声明、审计结论和正式提交：

```text
Shopper / ShopEnv raw event
    -> executor claim（untrusted）
    -> audit report（verified / rejected）
    -> state_committed event
    -> deterministic reducer
    -> authoritative shopping_state.json
```

核心事件类型包括：

- `task_started`；
- `shopper_asked`；
- `shopper_replied`；
- `search_executed`；
- `product_opened`；
- `variant_selected`；
- `evidence_observed`；
- `purchase_prepared`；
- `purchase_confirmed`；
- `purchase_rejected`；
- `executor_reported`；
- `audit_completed`；
- `state_committed`；
- `purchase_committed`。

State Engine 需要提供：

- Append-only event log；
- 确定性 reducer；
- 周期性 checkpoint；
- 从日志重放状态；
- 崩溃后恢复；
- 基于 `tool_call_id` 的幂等执行；
- 事件和状态 schema 校验。

Reducer 只根据 `state_committed` 和确定性的环境控制事件更新 authoritative state。`executor_reported` 中的模型声明即使格式正确，也不能直接将需求或商品条件标记为 satisfied。

### 6.4 Audited Intent Records

每条需求保存为带来源、状态和版本的结构化事实，而不是一段不断改写的自由文本。

示例：

```json
{
  "constraint_id": "color",
  "dimension": "option",
  "value": "白色",
  "source": "dialogue",
  "status": "active",
  "hardness": "hard",
  "revision": 6,
  "evidence_event_id": "event-38"
}
```

Intent records 需要处理：

- 新增需求；
- 修改需求；
- 否定旧需求；
- 当前需求和用户画像冲突；
- `active`、`unknown`、`conflicted`、`superseded` 等状态；
- 需求版本变化和来源追踪。

默认优先级为：

```text
本轮用户明确表达
    >
本轮初始任务描述
    >
用户长期画像
```

关键运行时不变量：

1. 最新的明确用户表达可以覆盖用户画像；
2. 被覆盖的旧需求不能继续参与当前购买判断；
3. 未知条件不能被视为已满足；
4. 模型自己的推测不能直接建立事实状态。

Manager 或语义抽取器只能提出 Intent update proposal。对于明确的预算数值、商品规格等字段，Auditor 优先使用确定性解析；对于偏好、否定、覆盖关系等语义字段，Auditor 在隔离上下文中对照 Shopper 原话核验。所有 active intent 必须保留原始 utterance 的 evidence reference。

### 6.5 Audited Candidate Evidence Records

每个候选商品及其规格拥有独立证据，避免不同候选的属性在长轨迹中相互污染。

示例：

```json
{
  "asin": "123456",
  "variant_revision": 3,
  "selected_options": {
    "color": "蓝色",
    "size": "40"
  },
  "requirements": {
    "color": {
      "status": "supported",
      "evidence_step": 17
    },
    "waterproof": {
      "status": "unknown"
    },
    "budget": {
      "status": "conflicted",
      "evidence_step": 19
    }
  }
}
```

每条证据至少绑定：

```text
requirement_id
product_id / asin
variant_revision
status
evidence_event_id
source_page
```

关键运行时不变量：

1. 商品 A 的证据不能用于证明商品 B；
2. 一个规格的证据不能用于证明另一个规格；
3. 只有公开页面或 Shopper 回复可以建立证据；
4. 模型生成的描述不能替代环境证据。

Context Projector 每一步只向模型提供：

- 当前有效需求；
- 尚未确认的需求；
- Top-K 候选；
- 每个候选的 `supported/conflicted/unknown` 条件；
- 当前页面商品、已选规格和价格；
- 当前购买提案状态。

完整轨迹保存在事件日志中，不反复塞回模型上下文。

Candidate records 不是模型的自由文本记忆，而是 authoritative shopping state 的领域 schema。环境中的结构化字段可以由确定性 validator 直接核验；需要自然语言判断的属性由独立 Auditor 核验。Executor 自己生成的商品介绍只能作为 claim，不能作为 evidence。

### 6.6 Two-Phase Purchase Guard

Agent 不能直接执行 `click[Buy Now]`。购买被改造为准备和提交两个阶段。

#### 阶段一：`prepare_purchase`

Agent 提交：

- 商品 ID；
- 当前规格；
- 当前价格；
- 满足每条需求的证据引用。

Harness 检查：

- 所有硬约束是否得到支持；
- 是否仍存在会影响购买的未知条件；
- 所有证据是否属于当前商品和规格；
- 页面上实际选择的规格是否与提案一致；
- 当前价格是否满足预算；
- 当前商品是否曾被用户拒绝；
- 当前需求版本是否与提案版本一致。

检查通过后，Harness 将结构化商品方案交给 Shopper 确认。

#### 阶段二：`commit_purchase`

一次有效确认必须绑定：

```text
proposal_id
intent_revision
product_id
variant_revision
price
```

确认之后，只要发生以下任意变化，确认立即失效：

- 用户增加或修改需求；
- 更换商品；
- 更换颜色、尺码、容量等规格；
- 价格变化；
- 用户拒绝当前方案。

只有 Guard 再次验证通过后，`commit_purchase` 才映射为 ShopEnv 中真实的 `click[Buy Now]`。

这一机制将“购买前请确认”的提示词要求转化为可执行的运行时协议。

---

## 7. DSH 中的工程落点

各模块在基础 DSH Harness 中的职责映射为：

| DSH 生命周期位置 | ShopHorizon 行为 |
|---|---|
| Session 初始化 | 创建事件日志，并将公开初始目标和用户画像提交为初始 audited state |
| Round manager | 在无环境工具的隔离上下文中生成 subtask contract |
| Executor launch | 创建 fresh context，只注入当前合同需要的 audited state、evidence 和工具 |
| Tool dispatch | 执行 `ask_shopper/search/click/prepare_purchase/commit_purchase` |
| Pre-execute | 校验角色权限、合同边界、工具参数和 Purchase Guard |
| Post-execute | 记录不可变 raw event 和 untrusted execution report，不直接更新完成状态 |
| Audit phase | 在独立只读上下文中检查 Shopper/ShopEnv evidence，输出 audit report |
| State commit | 确定性校验 audit report，生成 `state_committed` 事件并运行 reducer |
| Persistence | 保存 events、checkpoint、tool call 幂等记录 |
| Trace | 同时保存模型可见 observation 与 evaluator-only raw data |

模型不能直接修改 authoritative state。Manager 和 Executor 的输出只能提出动作或状态变更建议；Auditor 只能提供带证据的核验结论；最终状态变化必须经过确定性 Committer。Harness 还必须在代码层保证 Manager 无环境修改工具、Auditor 无状态修改工具、Executor 无直接 state commit 权限。

---

## 8. 研究问题与假设

### RQ1：可审计状态转换是否超越单纯提示词优化？

在 backbone、任务和任务级总预算不变时，完整 Harness 相比原始 DSH 和购物 checklist prompt，是否能够提高 Success Rate、`Rstrict` 和 `Rloose`？

**假设 H1：** Prompt-only baseline 只能有限改善行为，而带独立审计、fresh context 和强制购买协议的完整 Harness 能显著提高严格成功率；提升主要来自 hard constraints 和 option satisfaction。

### RQ2：独立审计和领域状态分别解决什么错误？

Audited intent、candidate evidence、fresh-context execution 和 Purchase Guard 各自对哪些错误类型有效？

**假设 H2：**

- Audited intent records 主要降低需求遗忘和画像冲突；
- Audited candidate records 主要降低无证据结论和跨商品证据污染；
- fresh-context execution 主要降低上下文污染、循环和错误自我延续；
- Purchase Guard 主要降低错误规格、未确认和过期确认购买。

### RQ3：可靠执行机制是否改善长任务稳定性？

Checkpoint、replay 和幂等执行能否在上下文压缩、重复工具调用和进程中断后保持一致状态？

**假设 H3：** 在故障注入测试中，恢复后的最终状态与无故障执行保持一致，重复工具调用不会产生重复购买或状态漂移。

---

## 9. 实验设计

### 9.1 主实验组

| 实验组 | 配置 |
|---|---|
| B0 | 原始 DSH Agent |
| Bprompt | B0 + 完整购物 checklist/system prompt；无外部 authoritative state、无审计、无工具拦截 |
| Bstate | 外部结构化购物状态 + Context Projector；状态接受 Executor 自我报告，无独立审计 |
| Baudit | Bstate + 独立 Shopping Auditor + verified-only state commit |
| Full | Baudit + bounded fresh-context rounds + Two-Phase Purchase Guard + durable execution |

同时从 Full 配置进行反向消融：

- Full without independent audit：允许 Executor 自我报告进入状态；
- Full without fresh context：保留单一增长 session；
- Full without candidate-bound evidence：候选证据不按商品和规格隔离；
- Full without Purchase Guard：恢复直接 `Buy Now`；
- Full without durable execution：关闭 checkpoint、replay 和幂等控制。

`Bprompt` 用于直接回答“效果是否只是来自修改提示词”；`Bstate` 与 `Baudit` 隔离独立验证的贡献；反向消融用于判断 fresh context、领域状态和交易约束的独立作用。

### 9.2 公平性控制

所有实验组保持以下条件一致：

- Shopping Agent 模型；
- Shopper Simulator 模型；
- Shopper prompt；
- temperature 和随机种子策略；
- 任务 ID；
- ShopEnv 数据和版本；
- 最大 action 数；
- 任务级总 token budget；Manager、Executor、Auditor 共享该预算，额外开销同时单独报告；
- 超时和重试策略。

开发期间使用 30 至 50 个固定任务快速迭代。正式实验优先运行第四场景的完整 eval 子集；若成本受限，至少固定抽取 200 个任务并公开任务 ID。对于存在采样随机性的实验，使用多个固定 seed，并报告配对结果。

### 9.3 统计报告

最终报告至少包含：

- 每个指标的绝对值；
- 相对 baseline 的 paired delta；
- 95% bootstrap confidence interval；
- 二元成功指标的配对显著性检验；
- 失败类型分布；
- 至少 3 个成功修复案例和 3 个仍然失败的案例。

---

## 10. 评测体系

### 10.1 第一层：官方任务结果

保留 ShopSimulator 官方指标：

- Success Rate；
- `Rstrict`；
- `Rloose`；
- Category Satisfaction；
- Attribute Satisfaction；
- Option Satisfaction；
- Price Satisfaction。

这一层回答“最终买对了吗”。

### 10.2 第二层：Harness 专项指标

#### 意图管理

- 需求遗忘率；
- 画像冲突处理正确率；
- 必要信息询问覆盖率；
- 无效询问率；
- 重复询问率；
- Intent revision 一致性；
- Unsupported Intent Commit Rate：没有 Shopper 原话支持却进入 active state 的需求比例。

#### 候选管理

- Trajectory Evidence Coverage；
- Best Candidate Coverage；
- Final Candidate Coverage；
- Final Variant Coverage；
- 跨商品证据污染率；
- 无证据属性声明率；
- Executor Claim False-Accept Rate：Executor 错误声明被 Auditor 接受的比例；
- Auditor False-Reject Rate：已有充分证据却被 Auditor 拒绝的比例。

#### 交易安全

- 错误规格购买率；
- 未确认购买率；
- 过期确认购买率；
- 用户拒绝后继续购买率；
- Guard 阻止的危险购买数量；
- Guard 误拦截率。

这一层回答“为什么买对或买错”。

### 10.3 第三层：效率与可靠性

- 平均步骤数；
- 重复搜索率；
- 无效 click 率；
- token 消耗；
- 延迟和成本；
- 崩溃恢复成功率；
- 状态重放一致性；
- 重复工具调用幂等成功率；
- 每轮 fresh-context 长度；
- Manager、Executor、Auditor 分角色 token 开销。

这一层回答“系统是否可用于真实工程”。

### 10.4 现有 rubric 和 eval 的定位

现有 eval 框架继续保留：

- 官方 reward；
- rubric 文件；
- 模型可见 trace 和 evaluator-only raw trace；
- eval runner；
- 报告生成框架。

但需要升级以下内容：

1. Rubric 判断必须绑定最终商品和最终规格；
2. 不能将多个候选商品的证据拼接为一次满足；
3. Multi-Turn 需要建立 requirement timeline；
4. 行为指标直接从实际 action trace 统计；
5. LLM Judge 只用于无法确定性判断的语义模糊项；
6. 每次实验保存 task、model、prompt、config 和代码版本 hash；
7. eval 输出按实验运行隔离，禁止后续小规模运行覆盖既有 manifest。

Rubric 永远只在 episode 结束后参与评测，不能进入 Agent prompt、Harness state 或模型可见 observation。

---

## 11. 可靠性与测试要求

### 11.1 单元测试

- Intent 优先级和覆盖规则；
- 同义表达归一化；
- 候选证据按 ASIN 隔离；
- 规格变化生成新 revision；
- Executor claim 不能直接产生 `state_committed`；
- 没有 evidence reference 的 audit fact 不能提交；
- 未知条件不能通过 Purchase Guard；
- 确认失效规则；
- reducer 的确定性。

### 11.2 集成测试

- 完整的“询问 -> 搜索 -> 推荐 -> 拒绝 -> 更新需求 -> 重新搜索 -> 确认 -> 购买”流程；
- Shopper Simulator 超时和重试；
- ShopEnv 无效 action；
- 确认后切换规格；
- 用户画像与当前需求冲突；
- Agent 尝试绕过 `prepare_purchase` 直接购买；
- Manager 尝试调用 ShopEnv 修改工具时被权限层拒绝；
- Auditor 尝试调用 click 或写入状态时被权限层拒绝；
- Executor 在报告中虚构属性时，Auditor 对照 public observation 拒绝该 claim。

### 11.3 故障注入

- 在任意 step 后终止进程并恢复；
- 重复发送同一个 `tool_call_id`；
- checkpoint 损坏后从事件日志重放；
- 模型上下文截断后依靠状态投影继续；
- Shopper 回复重复或延迟到达；
- 丢弃上一轮 Executor 原始轨迹后，从 audited state 启动下一轮。

恢复后的 authoritative state 必须与无故障执行一致，购买动作必须满足 exactly-once 语义。

---

## 12. 开发里程碑

### 第 1 周：跑通第四场景

- 实现 `ask_shopper`；
- 接入 Shopper Simulator；
- 正确注入用户画像；
- 支持拒绝、需求补充和最终确认；
- 输出完整可审计 trace。

验收条件：一个任务可以在没有真人参与的情况下完成完整多轮购物交互。

### 第 2 周：Shopping MEA Orchestrator

- Event schema；
- Manager、Executor、Auditor 角色接口；
- bounded subtask contract；
- fresh-context Executor；
- 角色工具权限白名单；
- round budget 和停止条件。

验收条件：Manager 不能修改环境，Auditor 不能修改环境或状态，Executor 不能直接提交 authoritative state；每轮 Executor 不携带上一轮原始推理轨迹。

### 第 3 周：Audited State Engine 与 Intent Records

- Append-only raw/proposal/audit/commit event；
- Deterministic Committer 和 reducer；
- 需求抽取接口；
- 来源、状态、优先级和 revision；
- 画像冲突处理；
- 未知需求和澄清队列；
- Shopper utterance evidence binding；
- Prompt-only 与 state-only 小规模实验。

### 第 4 周：Shopping Auditor 与 Candidate Evidence

- 候选、规格和证据 schema；
- 页面 observation 归约；
- 独立只读 Auditor；
- verified-only state commit；
- Top-K 候选投影；
- 跨候选证据隔离测试；
- Auditor 错误率人工校准；
- `Bstate` 与 `Baudit` 小规模实验。

### 第 5 周：Purchase Guard 与可靠执行

- `prepare_purchase`；
- `commit_purchase`；
- confirmation binding；
- Checkpoint；
- Replay；
- 幂等执行；
- 故障恢复和注入测试。

### 第 6 周：Eval V2 与项目展示

- Candidate-bound rubric；
- Requirement timeline；
- 完整消融实验；
- 统计报告；
- Trace Viewer 或 Dashboard；
- README、架构图和复现实验说明。

---

## 13. 最终交付物

仓库最终应至少包含：

1. 一条可直接运行的 CLI；
2. Baseline 和 Full Harness 的配置；
3. Multi-Turn + Personalization 环境适配器；
4. Shopper Simulator；
5. Shopping MEA Orchestrator 与角色权限控制；
6. Audited Event Log、Committer、Reducer、Checkpoint 和 Replay；
7. Audited Intent Records；
8. Audited Candidate Evidence Records；
9. Two-Phase Purchase Guard；
10. Eval V2 和实验 manifest；
11. Trace Viewer 或轻量 Dashboard；
12. Prompt-only、state-only、audited-state 和 Full 的对照实验；
13. 代表性 case study；
14. 完整 README 和复现脚本。

建议提供两个演示：

- **独立审计演示：** Executor 声称某个商品满足条件，但 Auditor 因缺少环境证据拒绝将其写入状态；
- **安全购买演示：** Agent 尝试在错误规格上购买，被 Guard 阻止；
- **恢复演示：** 任务在中途被终止，从 checkpoint 恢复后继续完成，并保持状态一致。

---

## 14. 项目创新点

项目不应被表述为“增加了一个记忆模块”或“修改了购物提示词”，而应强调以下设计：

### 14.1 Shopping-Specific Audited State Transitions

将模型声明、环境事实、审计结论和正式状态提交分离。只有独立核验且带环境证据的事实才能跨轮持久化。

### 14.2 Role- and Context-Isolated Shopping MEA

通过固定的 Manager、fresh-context Executor、read-only Auditor 和 deterministic Committer 划分计划、执行、验证和状态写入权限，阻止同一上下文自我执行、自我判断和自我确认。

### 14.3 Temporal Intent and Candidate-Bound Evidence

使用带来源、版本和原始话语引用的 intent records 解决画像与当前需求冲突；将商品证据绑定到具体商品和规格，阻止跨候选证据污染。

### 14.4 Revision-Bound Transaction Commit

将购买确认绑定到需求、商品、规格和价格版本，并由工具中间件强制执行，而不是依赖 Agent 遵守提示词。

### 14.5 Durable Shopping Execution

通过事件溯源、确定性重放、checkpoint 和幂等工具执行支持长任务可靠恢复。

独立审计和领域状态设计体现研究深度；权限控制、交易拦截和 durable execution 体现完整工程能力。

---

## 15. 风险与控制

### 风险 1：Shopper Simulator 的随机性影响公平比较

控制方式：固定模型、prompt、temperature 和 seed 策略；保存所有 Shopper 回复；对主结果进行多 seed 配对实验。

### 风险 2：Harness 通过隐藏字段意外泄漏答案

控制方式：运行时和 evaluator 使用不同数据对象；公开 observation 白名单；增加字段审计和泄漏测试。

### 风险 3：状态抽取仍然依赖 LLM，结果不稳定

控制方式：环境事实优先使用确定性解析；LLM 只提出或核验自然语言语义；Executor 与 Auditor 使用隔离上下文；每条状态保留来源、证据和冲突信息；人工标注小规模 audit set，报告 False-Accept 和 False-Reject，而不是假设 Auditor 永远正确。

### 风险 4：Purchase Guard 过于严格，导致 Agent 无法购买

控制方式：同时报告危险购买拦截率和误拦截率；区分 hard、soft 和 unknown 条件；允许 Agent 通过继续询问或浏览解除阻塞。

### 风险 5：项目范围继续膨胀

控制方式：先完成第四场景闭环和固定 Shopping MEA 主链，再实现展示层；自进化、训练、并行 swarm 和开放式多 Agent 协作不进入首版里程碑。

---

## 16. 秋招项目表述

### 简历项目名称

**ShopHorizon：个性化多轮购物 Agent 的可审计状态转换与交易安全 Harness**

### 简历描述模板

> 基于 DSH 和 ShopSimulator 构建面向 Multi-Turn + Personalization 的长程购物 Harness，将执行组织为权限隔离的 Manager-Executor-Auditor 轮次；设计 verified-only shopping state、商品/规格级证据绑定和版本化两阶段购买协议，并通过事件溯源、checkpoint、确定性重放和幂等工具调用实现可靠执行。在固定模型和任务级预算下，相比 prompt-only baseline，于 N 个任务上将 Rstrict 从 X 提升至 Y，并将无证据状态提交及错误规格购买率降低 Z%。

在实验结果产生前，不填写或虚构 `N/X/Y/Z`。

### 面试叙事

```text
从真实 trace 中发现长程购物失败
-> 提炼需求、候选和交易三类状态错位
-> 发现单一上下文同时执行和自我判定的问题
-> 设计权限隔离的 Manage-Execute-Audit-Commit 协议
-> 用 audited intent、candidate evidence 和 transaction state 实现购物领域状态
-> 在 DSH 中强制状态写入和购买不变量
-> 通过 prompt-only/state-only/audited/full 对照和消融实验验证
-> 通过恢复、重放和 Trace Viewer 展示工程完整度
```

---

## 17. 当前唯一优先事项

在继续扩展 eval 或研究自进化之前，首先完成：

```text
ask_shopper
    +
Shopper Simulator
    +
Profile 注入
    +
Multi-Turn 对话 trace
```

验收标准是：**在没有真人参与、没有向 Agent 泄漏隐藏目标或 rubric 的情况下，自动跑通一个完整的 Multi-Turn + Personalization 购物任务。**

该闭环完成后，第二优先级不是继续修改 prompt，而是实现最小 Shopping MEA 骨架：

```text
Manager 生成一轮合同
-> fresh Executor 执行
-> read-only Auditor 独立核验
-> deterministic Committer 更新状态
```

在这条链路中，至少通过四项硬验收：Manager 无环境修改工具、Auditor 无写工具、Executor claim 不能直接更新状态、未经过 Purchase Guard 的 Buy Now 不能到达 ShopEnv。
