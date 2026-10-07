FROM python:3.11-slim

WORKDIR /app

# 系统依赖（pymysql/sqlalchemy/jieba 均为纯 Python，无需编译；
# 仅安装运行时必要的证书与工具）
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/
COPY deploy/ ./deploy/

# 数据库默认走 SQLite（零依赖）；Railway 通过环境变量覆盖：
#   DB_TYPE=sqlite  DB_SQLITE_PATH=/data/resume_optimizer.db  + Volume 挂载 /data
ENV DB_TYPE=sqlite
ENV DB_SQLITE_PATH=/data/resume_optimizer.db

EXPOSE 8000
ENV PORT=8000

CMD ["sh", "deploy/startup.sh"]
