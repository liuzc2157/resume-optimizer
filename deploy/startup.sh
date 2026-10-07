#!/bin/sh
# Railway / 容器环境启动脚本：
# 1) 打印当前数据库配置（便于排查部署日志）；
# 2) SQLite 数据目录可写性检测：配置路径不可写（Volume 未挂载）时自动回退到
#    容器内 /app/data（保证服务必能启动）；Volume 正常挂载则用配置路径（持久化）；
# 3) 首次启动自动建库建表 + 幂等灌入种子岗位（已存在会跳过）；
# 4) 启动 FastAPI 服务。
set -e
echo "[startup] DB_TYPE=${DB_TYPE:-sqlite} DB_SQLITE_PATH=${DB_SQLITE_PATH:-/data/resume_optimizer.db}"

if [ "${DB_TYPE:-sqlite}" != "mysql" ]; then
  TARGET_DIR="$(dirname "${DB_SQLITE_PATH:-/data/resume_optimizer.db}")"
  if mkdir -p "$TARGET_DIR" 2>/dev/null && touch "$TARGET_DIR/.wtest" 2>/dev/null; then
    rm -f "$TARGET_DIR/.wtest"
    echo "[startup] 数据目录可写：$TARGET_DIR"
  else
    # Volume 未挂载时 /data 不可写 -> 回退容器内路径，保证能启动（重启后数据不持久）
    export DB_SQLITE_PATH="/app/data/resume_optimizer.db"
    mkdir -p /app/data
    echo "[startup] 配置目录不可写，已回退到：$DB_SQLITE_PATH"
  fi
fi

echo "[startup] 初始化数据库（建表 + 种子岗位，幂等）..."
python scripts/setup_local.py || echo "[startup] 数据库初始化非致命失败，继续启动"

echo "[startup] 启动 API 服务，端口 ${PORT:-8000}..."
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
