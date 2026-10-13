"""数据库连接层（SQLAlchemy + Postgres / SQLite）。

连接参数全部来自环境变量，方便本地 / Docker / Railway / Supabase 切换。

支持两种引擎：
- sqlite（默认，本地零依赖）：免安装、免密码、免建库，立刻能跑通全链路。
- postgres：云端生产引擎（Railway Postgres / Supabase），优先用 DATABASE_URL 连接。

环境变量
--------
    DB_TYPE          sqlite(默认) | postgres
    DATABASE_URL     Postgres 完整连接串（postgres 模式优先；Railway/Supabase 均提供）
    DB_HOST          默认 127.0.0.1            （仅 postgres，未设 DATABASE_URL 时）
    DB_PORT          默认 5432                 （仅 postgres）
    DB_USER          默认 postgres             （仅 postgres）
    DB_PASSWORD      必填（postgres 模式，无默认避免误连）
    DB_NAME          默认 resume_optimizer      （仅 postgres）
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

DB_TYPE: str = os.getenv("DB_TYPE", "sqlite").strip().lower()
DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT: str = os.getenv("DB_PORT", "5432")
DB_USER: str = os.getenv("DB_USER", "postgres")
DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
DB_NAME: str = os.getenv("DB_NAME", "resume_optimizer")
DB_SQLITE_PATH: str = os.getenv("DB_SQLITE_PATH", "./resume_optimizer.db")

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def build_engine() -> Engine:
    """根据 DB_TYPE 构造一个**独立**引擎（默认引擎外的额外连接池）。

    业务侧用 get_engine() 的共享引擎；像 LangGraph Checkpointer 这类需要
    与请求会话隔离连接池的场景，可调用本函数拿一个新引擎，避免与请求
    事务在同一个 SQLite 连接上交叉，产生“transaction within transaction”或原生崩溃。
    """
    if DB_TYPE == "sqlite":
        # 内存库（:memory: 或空路径）用 StaticPool：单连接跨线程共享，适合测试注入；
        # 文件库用 SingletonThreadPool：每个线程一条独立连接——LangGraph 会把节点
        # 丢进线程池执行，若全进程共用一条 SQLite 连接，跨线程 INSERT 会因连接状态
        # 串扰拿不到主键（FlushError / bad parameter）。
        from sqlalchemy.pool import SingletonThreadPool, StaticPool

        logger.info("[db] 使用 SQLite 引擎 -> %s", DB_SQLITE_PATH)
        use_static = DB_SQLITE_PATH in ("", ":memory:")
        return create_engine(
            f"sqlite:///{DB_SQLITE_PATH}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool if use_static else SingletonThreadPool,
            future=True,
        )
    # Postgres：优先用 DATABASE_URL（Railway Postgres / Supabase 都直接给完整连接串），
    # 未设置时再按 DB_* 分段拼接；postgres:// 与 postgresql:// 统一转成 psycopg2 方言。
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        db_url = (
            f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        )
    elif db_url.startswith("postgres://"):
        db_url = "postgresql+psycopg2://" + db_url[len("postgres://"):]
    elif db_url.startswith("postgresql://"):
        db_url = "postgresql+psycopg2://" + db_url[len("postgresql://"):]
    logger.info("[db] 使用 Postgres 引擎")
    return create_engine(
        db_url,
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
    """产出一个数据库会话（调用方负责关闭）。"""
    if _SessionLocal is None:
        get_engine()
    return _SessionLocal()


def db_session():
    """FastAPI 依赖：产出一个数据库会话，请求结束后自动关闭。"""
    s = get_session()
    try:
        yield s
    finally:
        s.close()


def ensure_database() -> None:
    """确保目标库存在（幂等，空操作）。

    SQLite：数据库文件由引擎自动创建；Postgres：数据库实例由 Railway / Supabase
    等平台预置，后端无需（也通常没有权限）建库，因此这里无需任何动作。
    """
    return


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
        "user_id": "user_id INTEGER NULL",
    },
    "analysis_cache": {
        "jd_ref": "jd_ref VARCHAR(64) NULL",
        "cache_version": "cache_version VARCHAR(64) NULL",
        "embedding": "embedding TEXT NULL",
        "user_id": "user_id INTEGER NULL",
    },
    "cache_stats": {
        "semantic_hits": "semantic_hits BIGINT NOT NULL DEFAULT 0",
    },
    "saved_resumes": {
        "sections": "sections JSON NULL",
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
            stmt = f"ALTER TABLE {table} ADD COLUMN {ddl}"
            try:
                with engine.begin() as conn:
                    conn.execute(text(stmt))
                logger.info("[db] 已为 %s 补加列 %s", table, col)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[db] 补加列 %s.%s 失败（可忽略）：%s", table, col, exc)


def init_db() -> None:
    """创建所有表（若尚不存在），初始化缓存统计单行，并写入示例 JD。

    幂等：可安全在每次启动时调用（ensure_database 为空操作）。
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


# 兼容旧名（部分脚本直接引用 _build_engine）
def _build_engine() -> Engine:
    """兼容别名：等价于 build_engine()。"""
    return build_engine()