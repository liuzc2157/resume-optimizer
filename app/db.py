"""数据库连接层（SQLAlchemy + PyMySQL / SQLite）。

连接参数全部来自环境变量，方便本地 / Docker / Railway / CloudBase 切换。

支持两种引擎：
- mysql（默认）：连真实 MySQL，用于生产 / 你的本地 MySQL / 云端 MySQL。
- sqlite：本地开发零依赖模式，免安装、免密码、免建库，立刻能跑通全链路。

环境变量
--------
    DB_TYPE          mysql(默认) | sqlite
    DB_HOST          默认 127.0.0.1            （仅 mysql）
    DB_PORT          默认 3306                 （仅 mysql）
    DB_USER          默认 root                 （仅 mysql）
    DB_PASSWORD      必填（mysql 模式，无默认避免误连）
    DB_NAME          默认 resume_optimizer      （仅 mysql）
    DB_SQLITE_PATH   默认 ./resume_optimizer.db（仅 sqlite）

本地纯开发（零依赖）跑法：
    DB_TYPE=sqlite python -m uvicorn app.main:app --port 8000
"""

import logging
import os

from sqlalchemy import Engine, create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, CacheStats, JD
# 示例岗位库种子（27 条，覆盖 12 个职业方向）；已抽到独立模块便于扩充
from .seed_data import SEED_JDS

logger = logging.getLogger(__name__)

DB_TYPE: str = os.getenv("DB_TYPE", "mysql").strip().lower()
DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT: str = os.getenv("DB_PORT", "3306")
DB_USER: str = os.getenv("DB_USER", "root")
DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
DB_NAME: str = os.getenv("DB_NAME", "resume_optimizer")
DB_SQLITE_PATH: str = os.getenv("DB_SQLITE_PATH", "./resume_optimizer.db")

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _build_engine() -> Engine:
    """根据 DB_TYPE 构造引擎。"""
    if DB_TYPE == "sqlite":
        from sqlalchemy.pool import StaticPool

        logger.info("[db] 使用 SQLite 引擎 -> %s", DB_SQLITE_PATH)
        return create_engine(
            f"sqlite:///{DB_SQLITE_PATH}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
    # MySQL 8 默认 caching_sha2_password，PyMySQL 已支持；charset 必须 utf8mb4
    url = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        f"?charset=utf8mb4"
    )
    logger.info("[db] 使用 MySQL 引擎 -> %s:%s/%s", DB_HOST, DB_PORT, DB_NAME)
    return create_engine(
        url,
        pool_pre_ping=True,   # 自动剔除失效连接，避免云数据库长时间空闲断连
        pool_recycle=1800,    # 30 分钟回收，配合云数据库
        future=True,
    )


def get_engine() -> Engine:
    """懒初始化并返回全局引擎（进程级单例）。"""
    global _engine, _SessionLocal
    if _engine is None:
        _engine = _build_engine()
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def get_session() -> Session:
    """FastAPI 依赖：产出一个数据库会话（调用方负责关闭）。"""
    if _SessionLocal is None:
        get_engine()
    return _SessionLocal()


def ensure_database() -> None:
    """（仅 MySQL）确保目标库存在；SQLite 模式为空操作。

    连到无库端点执行 CREATE DATABASE IF NOT EXISTS，避免手动建库。
    需要所用账号具备建库权限（本地 root 通常满足）。
    """
    if DB_TYPE != "mysql":
        return
    root = create_engine(
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/?charset=utf8mb4",
        pool_pre_ping=True,
        future=True,
    )
    with root.connect() as conn:
        conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
                f"DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci"
            )
        )
        conn.execute(text(f"USE `{DB_NAME}`"))
    logger.info("[db] 数据库 %s 已确保存在", DB_NAME)


def seed_jds(s: Session) -> int:
    """按**标题**幂等写入示例 JD。

    与早期「库为空才写入」不同：这里逐条比对标题，已存在的跳过、新条目补齐。
    这样岗位库扩充（新增职业/等级）时，老数据保留、新数据自动入库，
    用户自己保存的 JD（source=user）也不会被覆盖。
    """
    existing = {t for (t,) in s.execute(select(JD.title)).all()}
    added = 0
    for d in SEED_JDS:
        if d["title"] in existing:
            continue
        s.add(JD(**d))
        added += 1
    if added:
        s.commit()
    logger.info(
        "[db] 岗位库：库内 %d 条，本次新增 %d 条（种子共 %d 条）",
        len(existing),
        added,
        len(SEED_JDS),
    )
    return added


# 后续迭代新增的列（create_all 不会改已存在的表，需显式 ADD COLUMN）
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "jds": {
        "source": "source VARCHAR(32) NULL DEFAULT 'seed'",
        "embedding": "embedding TEXT NULL",
        "rag_text": "rag_text TEXT NULL",
    },
    "analysis_cache": {
        "jd_ref": "jd_ref VARCHAR(64) NULL",
        "cache_version": "cache_version VARCHAR(64) NULL",
        "embedding": "embedding TEXT NULL",
    },
    "cache_stats": {
        "semantic_hits": "semantic_hits BIGINT NOT NULL DEFAULT 0",
    },
}


def ensure_columns() -> None:
    """为已存在的表补加新增列（幂等，缺哪列加哪列）。

    场景：项目迭代给 analysis_cache / cache_stats 加了列，但线上库是旧表，
    Base.metadata.create_all() 只建新表、不改旧表，故需这一步迁移。
    """
    engine = get_engine()
    insp = inspect(engine)
    for table, cols in _ADDED_COLUMNS.items():
        if not insp.has_table(table):
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for col, ddl in cols.items():
            if col in existing:
                continue
            stmt = (
                f"ALTER TABLE `{table}` ADD COLUMN {ddl}"
                if DB_TYPE == "mysql"
                else f"ALTER TABLE {table} ADD COLUMN {ddl}"
            )
            try:
                with engine.begin() as conn:
                    conn.execute(text(stmt))
                logger.info("[db] 已为 %s 补加列 %s", table, col)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[db] 补加列 %s.%s 失败（可忽略）：%s", table, col, exc)


def init_db() -> None:
    """创建所有表（若尚不存在），初始化缓存统计单行，并写入示例 JD。

    幂等：可安全在每次启动时调用。MySQL 模式会先 ensure_database。
    """
    ensure_database()
    engine = get_engine()
    Base.metadata.create_all(engine)
    ensure_columns()
    with Session(engine) as s:
        if not s.scalar(select(CacheStats).where(CacheStats.id == 1)):
            s.add(CacheStats(id=1, hits=0, misses=0))
            s.commit()
            logger.info("[db] 初始化 cache_stats 单行")
        seed_jds(s)
    logger.info("[db] 表结构已就绪（引擎=%s）", DB_TYPE)


def check_connection() -> bool:
    """探活：能连上且能执行简单查询返回 True。"""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(select(1))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[db] 连接检查失败：%s", exc)
        return False
