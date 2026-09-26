#!/usr/bin/env bash
# verify 一次性服务入口：代码测试 -> 构建校验 -> API 冒烟。
# 任一步骤失败即非零退出；全部通过则以 0 退出。
set -euo pipefail

echo "==> [1/3] 代码测试（pytest）"
python -m pytest tests -v

echo "==> [2/3] 构建校验（编译后端源码并导入应用）"
python -m compileall -q app tests
python -c "from app.main import app; print('后端应用构建/导入成功：', app.title)"

echo "==> [3/3] API 冒烟测试（经由真实运行的服务）"
python smoke.py

echo "==> verify 全部通过，退出码 0"
