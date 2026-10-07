#!/bin/sh
# Railway / 容器环境启动脚本：
# 1) 首次启动自动建库建表 + 幂等灌入种子岗位（已存在会跳过，重复启动安全）；
# 2) 启动 FastAPI 服务。
set -e
echo "[startup] 初始化数据库（建表 + 种子岗位，幂等）..."
python scripts/setup_local.py || echo "[startup] 数据库初始化非致命失败，继续启动"
echo "[startup] 启动 API 服务，端口 ${PORT:-8000}..."
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
