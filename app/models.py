"""SQLAlchemy ORM 模型定义。

表结构与 deploy/init.sql 保持一致；这里用 ORM 做读写，
init.sql 主要负责建库与写入示例 JD 种子数据。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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
    """JD 岗位库一行记录。

    user_id：NULL 表示内置种子岗位（全局共享）；非 NULL 表示某位用户
    自己上传/保存的岗位，仅创建者可见、可匹配。
    """

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
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_cat_level", "position_category", "experience_level"),
    )


class AnalysisCache(Base):
    """分析结果缓存一行记录（命中即直接返回）。

    user_id：缓存按用户隔离——同一份简历+JD 的分析结果只在本用户内复用，
    绝不跨用户返回（简历是最敏感的个人信息，跨用户命中属于数据泄露）。
    """

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
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
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
        Index("idx_user_jd", "user_id", "jd_id"),
        # 语义检索按 (分区, 版本) 扫描，故建联合索引
        Index("idx_semantic", "jd_ref", "cache_version"),
    )


class MatchLog(Base):
    """简历 -> 岗位匹配日志（只存哈希，不含简历原文）。"""

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


class User(Base):
    """注册用户。

    plan: free / pro（预留升级位）；daily_quota: 每日可消费配额的调用次数，
    0 表示不限。密码仅存 PBKDF2 哈希，绝不存明文。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    plan: Mapped[str] = mapped_column(String(16), nullable=False, default="free")
    daily_quota: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 同意隐私政策的确认时间戳（注册时勾选后写入）
    consent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("idx_username", "username"),)


class EmailCode(Base):
    """邮箱验证码（注册 / 找回密码共用一张表）。

    - code_hash 只存 sha256，不存明文；attempts 防爆破（错误 5 次作废）。
    - 同一邮箱同一用途同时只保留最近一条有效码：新发码会作废旧码。
    - SMTP 未配置时（本地开发），send-code 接口会在响应里返回 dev_code 便于联调；
      生产配置 SMTP 后不再返回，只能走邮箱收取。
    """

    __tablename__ = "email_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="register")
    code_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (Index("idx_email_purpose", "email", "purpose"),)


class UsageLog(Base):
    """每次业务调用的用量记录（鉴权 + 配额 + 计费 + 审计共用）。

    只记录元信息与 token 数，不存简历/JD 原文，避免把敏感内容平铺到
    用量表里扩大泄露面。
    """

    __tablename__ = "usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (Index("idx_user_created", "user_id", "created_at"),)


class ChatCheckpoint(Base):
    """对话 Agent 的检查点（LangGraph Checkpointer 的 SQLAlchemy 持久化）。

    保存 LangGraph 的完整 checkpoint（含 channel_values，序列化为 base64 JSON），
    让多轮对话在进程重启 / 多实例部署后仍可恢复。
    """

    __tablename__ = "chat_checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # base64(serde(checkpoint))
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)  # base64(serde(metadata))
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("thread_id", "checkpoint_ns", "checkpoint_id", name="uk_thread_checkpoint"),
        Index("idx_thread", "thread_id", "checkpoint_ns"),
    )


class ChatWrite(Base):
    """对话 Agent 的待应用写入（LangGraph put_writes 的持久化）。

    一次工具执行产生的中间写入，与所属 checkpoint 关联存储。
    """

    __tablename__ = "chat_writes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_ns: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_path: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    writes: Mapped[str] = mapped_column(Text, nullable=False)  # JSON 数组（base64 序列化值）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "task_path",
            name="uk_thread_write",
        ),
    )


class SavedResume(Base):
    """用户保存的简历（简历库，每用户最多 3 份）。

    三份覆盖典型场景：主简历 + 不同求职方向的备选。优化时可直接调用，
    改满意后覆盖更新，不产生过期版本。name 为空时展示「未命名简历」。
    """

    __tablename__ = "saved_resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # sections: 结构化简历字段（基本信息/教育背景/工作经历/项目经历/技能/证书/自我评价）
    sections: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    position_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    experience_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("idx_user", "user_id"),)
