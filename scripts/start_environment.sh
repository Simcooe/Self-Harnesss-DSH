#!/usr/bin/env bash
# 启动 ShopSimulator 服务（:5700）。首次请先运行 scripts/setup.sh。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHOPSIM_ROOT="$ROOT/environments/ShopSimulator"
SHOP_ENV_ROOT="$SHOPSIM_ROOT/shop_env"
SHOPSIM_VENV="$SHOPSIM_ROOT/.venv-shopsim"

if [[ ! -x "$SHOPSIM_VENV/bin/python" ]]; then
  echo "ShopSimulator 未安装。请先运行: bash scripts/setup.sh" >&2
  exit 1
fi

INDEX_PATH="$SHOP_ENV_ROOT/search_engine/products.sqlite3"
if [[ ! -f "$INDEX_PATH" ]]; then
  echo "搜索索引缺失: $INDEX_PATH" >&2
  echo "请先运行: bash scripts/setup.sh" >&2
  exit 1
fi

export SHOP_ENVIRONMENT_VERSION=shopsimulator-environment-v2.1
export SHOP_ENV_CONFIG="$SHOP_ENV_ROOT/configs/environment.json"
export SHOP_SEARCH_INDEX="$INDEX_PATH"
export SHOP_MAX_STEPS="${SHOP_MAX_STEPS:-35}"
export SHOPSIM_ENV_SLOTS="${SHOPSIM_ENV_SLOTS:-8}"
export SHOPSIM_PORT="${SHOPSIM_PORT:-5700}"

cd "$SHOP_ENV_ROOT/shop_env"
exec "$SHOPSIM_VENV/bin/python" pack_api.py
