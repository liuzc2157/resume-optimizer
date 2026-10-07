"""SQLAlchemy ORM 模型定义。

表结构与 deploy/init.sql 保持一致；这里用 ORM 做读写，
init.sql 主要负责建库与写入示例 JD 种子数据。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CHAR,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    JSON,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """声明式基类。"""


class JD(Base):
    """JD 岗位库一行记录。"""

    __tablename__ = "jds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_category: Mapped[str] = mapped_column(String(64), nullable=False)
    experience_level: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    skills: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # source: seed=内置示例岗位；user=用户自己传入并保存的 JD
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, default="seed")
    # embedding: JD 文本的向量（JSON 数组），由 RAG 子系统在入库/建索引时生成。
    # 存放在库内（而非临时文件），云端重部署（Railway/CloudBase）不丢索引。
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    # rag_text: 经「入库切片预处理 Prompt」清洗后的精简 JD 文本，用于向量化与召回展示。
    rag_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_cat_level", "position_category", "experience_level"),
    )


class AnalysisCache(Base):
    """分析结果缓存一行记录（命中即直接返回）。"""

    __tablename__ = "analysis_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    resume_hash: Mapped[str] = mapped_column(CHAR(32), nullable=False)
    # jd_ref: 岗位引用 'jd:<id>' 或 'text:<md5>'。语义检索只在同 jd_ref 分区内进行，
    # 避免把 A 岗位的分析错配给 B 岗位（语义缓存最典型的误命中风险）。
    jd_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # cache_version: prompt / 模型 / 向量维度变更时整体失效旧缓存，防止陈旧结果污染。
    cache_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # embedding: 简历文本的向量（JSON 数组），供语义近似命中检索。
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    jd_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    experience_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    rewrite: Mapped[str | None] = mapped_column(Text, nullable=True)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_hit_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("cache_key", name="uk_cache_key"),
        Index("idx_jd", "jd_id"),
        # 语义检索按 (分区, 版本) 扫描，故建联合索引
        Index("idx_semantic", "jd_ref", "cache_version"),
    )


class MatchLog(Base):
    """简历 -> 岗位匹配日志。"""

    __tablename__ = "match_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resume_hash: Mapped[str] = mapped_column(CHAR(32), nullable=False)
    position_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    experience_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    matched_jd_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    matched_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (Index("idx_resume", "resume_hash"),)


class CacheStats(Base):
    """缓存命中统计（单行累计）。"""

    __tablename__ = "cache_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    hits: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    misses: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # semantic_hits: 通过「语义近似」命中的次数（与 hits 精确命中分开统计，
    # 便于观察语义层带来的增量收益与调阈值）。
    semantic_hits: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
