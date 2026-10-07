#!/bin/sh
# Railway / 容器环境启动脚本：
# 1) 打印当前数据库配置（便于排查部署日志）；
# 2) 自动创建 SQLite 数据目录（Volume 未挂载时兜底，避免打不开库文件）；
# 3) 首次启动自动建库建表 + 幂等灌入种子岗位（已存在会跳过）；
# 4) 启动 FastAPI 服务。
set -e
echo "[startup] DB_TYPE=${DB_TYPE:-sqlite} DB_SQLITE_PATH=${DB_SQLITE_PATH:-/data/resume_optimizer.db}"

# SQLite 模式：确保数据目录存在（若 Railway Volume 已挂载 /data 则持久化，未挂载也能跑）
if [ "${DB_TYPE:-sqlite}" != "mysql" ]; then
  mkdir -p "$(dirname "${DB_SQLITE_PATH:-/data/resume_optimizer.db}")"
fi

echo "[startup] 初始化数据库（建表 + 种子岗位，幂等）..."
python scripts/setup_local.py || echo "[startup] 数据库初始化非致命失败，继续启动"

echo "[startup] 启动 API 服务，端口 ${PORT:-8000}..."
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
