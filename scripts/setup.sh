#!/usr/bin/env bash
# Self-Harness DSH — 一键安装脚本。
#
# 三件事：
#   1. clone dsh 上游（固定 commit）到 deps/deepseek-harness；
#   2. 装 ShopSimulator 环境：建 venv、装依赖、解压产品数据、建搜索索引；
#   3. 初始化 dsh headless profile，并把 shop-tools 插件装进去。
#
# 用法:
#   bash scripts/setup.sh
#
# 依赖: git, uv, pnpm, node (>=22.19)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPS_DIR="$ROOT/deps"
DSH_COMMIT="b150a551b8d465e31e418e1b2eaf5e79bbb7d28e"
DSH_DIR="$DEPS_DIR/deepseek-harness"

SHOPSIM_ROOT="$ROOT/environments/ShopSimulator"
SHOP_ENV_ROOT="$SHOPSIM_ROOT/shop_env"
SHOPSIM_VENV="$SHOPSIM_ROOT/.venv-shopsim"
COMPRESSED="$SHOP_ENV_ROOT/data/fine_items_eval_train_all.json.gz"
PRODUCTS="$SHOP_ENV_ROOT/data/items_eval_train.json"
EXPECTED_SHA256="57b10950a0064d16c81535a1d764a75879a508d250dde8a2a1787c5e6045559f"

echo "==> [1/3] 拉取 dsh 上游 (commit ${DSH_COMMIT:0:12})"
mkdir -p "$DEPS_DIR"
if [[ ! -d "$DSH_DIR/.git" ]]; then
  git clone https://github.com/deepseek-ai/deepseek-harness.git "$DSH_DIR"
fi
git -C "$DSH_DIR" checkout "$DSH_COMMIT" 2>/dev/null || true

echo "==> [1/3] 安装 dsh 依赖 (pnpm install)"
( cd "$DSH_DIR" && pnpm install )

echo "==> [2/3] 建 ShopSimulator 虚拟环境 (python 3.10)"
if [[ ! -x "$SHOPSIM_VENV/bin/python" ]]; then
  uv venv --python 3.10 "$SHOPSIM_VENV"
fi
uv pip install --python "$SHOPSIM_VENV/bin/python" \
  --default-index https://mirrors.aliyun.com/pypi/simple/ \
  -r "$SHOP_ENV_ROOT/requirements.txt"

echo "==> [2/3] 解压产品数据并校验"
if [[ ! -f "$PRODUCTS" ]]; then
  tmp="$PRODUCTS.preparing"
  rm -f "$tmp"
  gzip -cd "$COMPRESSED" > "$tmp"
  actual="$(shasum -a 256 "$tmp" | awk '{print $1}')"
  if [[ "$actual" != "$EXPECTED_SHA256" ]]; then
    echo "产品数据 SHA-256 校验失败: $actual" >&2
    rm -f "$tmp"
    exit 1
  fi
  mv "$tmp" "$PRODUCTS"
fi

echo "==> [2/3] 建搜索索引"
( cd "$SHOP_ENV_ROOT" && PYTHONPATH=. "$SHOPSIM_VENV/bin/python" scripts/build_index.py )

echo "==> [3/3] 初始化 dsh profile 并安装 shop-tools 插件"
export DSH_HOME="$ROOT/.dsh-home"
( cd "$DSH_DIR" && pnpm dsh plugin --profile headless add "$ROOT" )

echo ""
echo "完成。"
echo "  1) 复制 .env.example 为 .env 并填 DEEPSEEK_API_KEY"
echo "  2) 启动环境: bash scripts/start_environment.sh"
echo "  3) 跑任务:   bash scripts/run_task.sh \"<任务指令>\""
