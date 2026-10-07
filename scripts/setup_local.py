"""本地数据库初始化脚本（一键建库/建表/灌种子）。

用法
----
1) MySQL 模式：
   - 编辑项目根目录 .env（或 export）填好 DB_USER / DB_PASSWORD 等
   - 运行：python scripts/setup_local.py
   脚本会自动 CREATE DATABASE（若不存在）+ 建表 + 写入示例 JD + 初始化命中统计。

2) SQLite 模式（DB_TYPE=sqlite）：
   - DB_TYPE=sqlite DB_SQLITE_PATH=./resume_optimizer.db python scripts/setup_local.py

无需手动执行 deploy/init.sql（种子已内置在 app/db.py）。
"""

import os
import sys

sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.db import ensure_database, get_session, init_db  # noqa: E402
from app.models import JD  # noqa: E402
from sqlalchemy import select  # noqa: E402


def main() -> None:
    ensure_database()
    init_db()
    with get_session() as s:
        n_jd = len(s.execute(select(JD)).scalars().all())
    print(f"[setup] 初始化完成：JD 岗位数 = {n_jd}")
    print("[setup] 现在可以启动后端：")
    print("        MySQL  : uvicorn app.main:app --reload --port 8000")
    print("        SQLite: DB_TYPE=sqlite DB_SQLITE_PATH=./resume_optimizer.db \\")
    print("                 uvicorn app.main:app --reload --port 8000")


if __name__ == "__main__":
    main()
