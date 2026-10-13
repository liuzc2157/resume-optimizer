FROM python:3.11-slim

WORKDIR /app

# 系统依赖（psycopg2-binary/sqlalchemy/jieba 均无需编译；
# 仅安装运行时必要的证书与工具）
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/
COPY deploy/ ./deploy/

# 数据库默认走 SQLite（零依赖）；云端（Railway/Supabase）通过环境变量切换 Postgres：
#   DB_TYPE=postgres  DATABASE_URL=<Postgres 连接串>   （无需改代码，库已由平台预置）
#   本地/轻量部署仍可用：DB_TYPE=sqlite  DB_SQLITE_PATH=/data/resume_optimizer.db  + Volume 挂载 /data
ENV DB_TYPE=sqlite
ENV DB_SQLITE_PATH=/data/resume_optimizer.db

EXPOSE 8000
ENV PORT=8000

CMD ["sh", "deploy/startup.sh"]
