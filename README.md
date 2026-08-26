# Self-Harness DSH

将 Self-Harness 的自进化循环迁移到 DeepSeek Harness（dsh）的独立实现。
被进化的 harness 是 dsh 的一个 profile + bundle；任务环境是 ShopSimulator
（HTTP 服务，语言无关）。

## 目录结构

```
self-harness-dsh/
├── src/shop-tools.js     # dsh 工具插件：把 ShopSimulator HTTP 协议注册成模型工具
├── cordis.patch.yml      # bundle 的 patch 层：把工具插件插入 profile
├── package.json          # dsh bundle 声明（"dsh": {"bundle": {"patch": ...}}）
├── .env.example          # 配置模板（base url / api key / model）
├── loop/                 # [待写] Self-Harness 三段循环（workflow/diagnosis/proposer/acceptance）
└── scripts/              # [待写] 启动/编排脚本
```

## 前置条件

1. dsh 仓库已 `pnpm install`（见 `deepseek-harness/`）。
2. ShopSimulator 服务已起在 `http://127.0.0.1:5700`。
3. `.env` 里已填 `DEEPSEEK_API_KEY`（本文件 gitignore）。

## 安装插件到 profile

```sh
export DSH_HOME="$(pwd)/.dsh-home"
cd <dsh-checkout>
pnpm dsh plugin --profile headless add "<本目录绝对路径>"
```

验证 compose（无需 API key）：

```sh
pnpm dsh --profile headless --dump-config
```

## 运行

```sh
export DSH_HOME="$(pwd)/.dsh-home"
export SHOPSIM_BASE_URL=http://127.0.0.1:5700
export DEEPSEEK_API_KEY=...
export DSH_MODEL=deepseek-v4-pro
pnpm dsh --profile headless "<购物任务指令>"
```

## 工具清单（12 个）

`search_products` `open_product` `select_option` `view_description`
`view_features` `view_reviews` `view_attributes` `next_page` `prev_page`
`back_to_search` `buy_now` `finish_without_purchase`
