# Self-Harness DSH

将 Self-Harness 的自进化循环迁移到 DeepSeek Harness（dsh）的独立实现。
被进化的 harness 是 dsh 的一个 profile + bundle；任务环境是 ShopSimulator
（HTTP 服务，语言无关）。

## 目录结构

```
self-harness-dsh/
├── src/shop-tools.js          # dsh 工具插件：ShopSimulator 的 3 个原生动作 → 模型工具
├── cordis.patch.yml           # bundle patch 层：把工具插件插入 profile
├── package.json               # dsh bundle 声明
├── environments/ShopSimulator # ShopSimulator v2 快照（自包含，含 19MB 产品数据）
├── scripts/
│   ├── setup.sh               # 一键安装（clone dsh + 装环境 + 初始化 profile）
│   ├── start_environment.sh   # 启动 ShopSimulator 服务 :5700
│   ├── run_task.sh            # 跑单个购物任务（租 slot → 跑 → 释放）
│   ├── run_parallel.sh        # 并行跑多个任务
│   └── export_trace.py        # session.jsonl.zstd → 双视角 trace（model/raw）
└── .env.example               # 配置模板
```

## 快速开始

```bash
# 1. 一键安装（clone dsh + 装环境 + 建索引 + 装插件）
bash scripts/setup.sh

# 2. 配置模型密钥
cp .env.example .env
#    编辑 .env，填 DEEPSEEK_API_KEY

# 3. 启动环境（另开终端，保持运行）
bash scripts/start_environment.sh

# 4. 跑任务
bash scripts/run_task.sh "帮我推荐一款适合5岁左右小孩的乳胶枕头，预算1000元以下。"
```

## 前置依赖

- `git`、`uv`（Python 包管理）、`pnpm`、`node`（>=22.19）

## 工具（3 个原生动作）

`search` / `click` / `finish` —— 对应 ShopSimulator 的 `search[keywords]` /
`click[value]` / `finish[reason]`。

## Trace 双视角

`scripts/export_trace.py` 把 dsh 的 `session.jsonl.zstd` 导出成两个视角：

- `model_trace`：模型实际看到的（observation 已裁掉终局 reward/goal）
- `raw_trace`：环境原生完整返回（含 goal、reward_detail、progress 等，供诊断/评测）

```bash
python scripts/export_trace.py <session.jsonl.zstd> [out.json]
```
