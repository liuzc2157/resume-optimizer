"""Pydantic 请求/响应模型（新增缓存与匹配相关字段）。"""

from typing import Any, List, Optional

from pydantic import BaseModel, Field


# ---------------- 任务型（/optimize） ----------------
class OptimizeRequest(BaseModel):
    """POST /optimize 请求体。

    三种使用方式（优先级 jd_text > jd_id > 智能匹配）：
    1. 直接传 jd_text；
    2. 传 jd_id 指定库内岗位；
    3. 只传 position_category / experience_level，由系统从岗位库智能匹配最合适 JD。
    """

    resume_text: str = Field(..., min_length=1, description="候选人的简历原文")
    jd_text: Optional[str] = Field(None, description="目标岗位 JD 原文（可选）")
    jd_id: Optional[int] = Field(None, description="指定库内 JD 的 id（可选）")
    position_category: Optional[str] = Field(None, description="目标岗位类别，如 后端/前端/算法")
    experience_level: Optional[str] = Field(None, description="目标经验等级，如 初级/中级/高级/资深")
    intensity: Optional[str] = Field(
        "enhance",
        description="改写强度：light=轻推微调 / enhance=关键词增强（默认） / rewrite=整篇重写。"
        "不同强度缓存互相隔离，不会串结果。",
    )
    save_jd: bool = Field(
        True,
        description="传入 jd_text 时是否保存进岗位库。保存后下次同样岗位可直接复用，"
        "类别/等级未填会自动推断。设为 False 则不入库（一次性分析）。",
    )


class MatchedJDSchema(BaseModel):
    jd_id: int
    title: str
    position_category: str
    experience_level: str
    similarity: Optional[float] = None


class ScoreDimension(BaseModel):
    """单一评分维度（多维度 ATS 评分的组成项）。"""

    key: str
    label: str
    score: float
    weight: float
    detail: str
    hits: Optional[List[str]] = Field(
        None, description="关键词/技能命中列表（仅 keyword_match 维度返回，供前端高亮）"
    )
    misses: Optional[List[str]] = Field(
        None, description="关键词/技能缺失列表（仅 keyword_match 维度返回，供前端提示补齐）"
    )


class ScoreBreakdown(BaseModel):
    """多维度评分明细。"""

    total: int = Field(..., description="加权总分 0-100（确定性计算，非 LLM 生成）")
    dimensions: List[ScoreDimension] = Field(default_factory=list)


class OptimizeResponse(BaseModel):
    """POST /optimize 响应体。"""

    score: int = Field(..., description="ATS 多维加权分（1-100），确定性规则计算")
    llm_score: Optional[int] = Field(
        None,
        description="LLM 自评分数（分析报告里的 SCORE 行）。与 ATS 分口径不同，"
        "两者不一致属正常：ATS 分按简历硬指标算，LLM 分偏主观整体判断。",
    )
    analysis: str = Field(..., description="匹配度分析报告")
    rewrite: str = Field(..., description="改写建议与优化后的简历段落")
    cache_hit: bool = Field(False, description="True 表示命中缓存、未调用 LLM")
    cache_source: str = Field("miss", description="memory / db / semantic / miss")
    cache_similarity: float = Field(0.0, description="语义命中时的相似度（0 表示非语义命中）")
    matched_jd: Optional[MatchedJDSchema] = Field(None, description="实际命中的岗位")
    matches: List[MatchedJDSchema] = Field(default_factory=list, description="候选岗位 Top-N")
    breakdown: Optional[ScoreBreakdown] = Field(None, description="多维度 ATS 评分明细")
    messages: List[Any] = Field(default_factory=list, description="Agent 运行日志")


# ---------------- 智能匹配（/match） ----------------
class MatchRequest(BaseModel):
    resume_text: str = Field(..., min_length=1)
    position_category: Optional[str] = None
    experience_level: Optional[str] = None
    top_n: int = Field(5, ge=1, le=20)
    use_rag: bool = Field(
        False,
        description="是否用 RAG 向量检索召回候选岗位（语义匹配）。默认 False：沿用关键词"
        "重叠 + 技能加权的确定性匹配；设 True 后按简历向量召回最相似的 Top-K JD。",
    )
    rag_top_k: Optional[int] = Field(
        None, description="RAG 模式下的召回数量；留空则等于 top_n"
    )


class MatchResponse(BaseModel):
    matches: List[MatchedJDSchema]
    rag_query: Optional[str] = Field(
        None, description="RAG 模式下用于检索的简历提炼文本（仅 use_rag=True 时返回）"
    )
    mode: str = Field("keyword", description="本次匹配所用模式：keyword / rag")


# ---------------- RAG 向量检索（/rag/retrieve） ----------------
class RagItem(BaseModel):
    """RAG 召回的单条 JD，字段与 MatchedJDSchema 兼容。"""

    jd_id: int
    title: str
    position_category: str
    experience_level: str
    similarity: float = Field(..., description="与简历向量的余弦相似度 0-1")
    rag_text: str = Field("", description="该 JD 经入库清洗后的精简文本（便于解释召回原因）")


class RagRetrieveRequest(BaseModel):
    resume_text: str = Field(..., min_length=1, description="候选人的简历原文")
    top_k: int = Field(5, ge=1, le=20, description="召回的相似 JD 数量")
    category: Optional[str] = Field(None, description="按岗位类别过滤召回（metadata 过滤，Pinecone 服务端生效）")
    level: Optional[str] = Field(None, description="按经验等级过滤召回（如 初级/中级/高级）")


class RagRetrieveResponse(BaseModel):
    query_text: str = Field(..., description="经「检索查询预处理」提炼后的简历检索文本")
    items: List[RagItem] = Field(default_factory=list, description="按相似度降序的候选 JD")


# ---------------- 岗位自动分类（/classify） ----------------
class CategoryScore(BaseModel):
    """单个候选类别的关键词证据。"""

    category: str
    hits: int = Field(..., description="命中的关键词数量")
    confidence: float = Field(..., description="该类别占全部关键词证据的比例 0-1")


class ClassifyRequest(BaseModel):
    resume_text: str = Field(..., min_length=1, description="待分类的简历原文")
    top_n: int = Field(5, ge=1, le=20, description="返回的候选类别数量上限")


class ClassifyResponse(BaseModel):
    category: str = Field(..., description="最佳判定类别，无命中时为「通用」")
    level: str = Field(..., description="推断的经验等级")
    confidence: float = Field(..., description="最佳类别的置信度 0-1")
    category_scores: List[CategoryScore] = Field(default_factory=list, description="各命中类别及置信度（降序）")
    level_reason: dict = Field(default_factory=dict, description="等级推断依据：years / keywords")


# ---------------- 对话型（/chat） ----------------
class ChatRequest(BaseModel):
    """POST /chat 请求体。thread_id 为会话标识，首轮可不传（自动生成）。

    可选携带 position_category / experience_level / jd_id，
    让对话 Agent 在简历/JD 缺失时也能从岗位库智能匹配。
    """

    message: str = Field(..., min_length=1, description="用户本轮发送的消息")
    thread_id: str | None = Field(None, description="会话 ID，续聊时传入上一次响应返回的值")
    position_category: Optional[str] = Field(None, description="目标岗位类别（可选）")
    experience_level: Optional[str] = Field(None, description="目标经验等级（可选）")
    jd_id: Optional[int] = Field(None, description="指定库内 JD id（可选）")


class ChatResponse(BaseModel):
    thread_id: str = Field(..., description="会话 ID，后续请求需携带以延续上下文")
    reply: str = Field(..., description="Agent 本轮的回复")
    cache_hit: bool = Field(False, description="本轮分析是否命中缓存")


# ---------------- JD 岗位库管理 ----------------
class JDCreate(BaseModel):
    position_category: str
    experience_level: str
    title: str
    content: str
    skills: Optional[list] = None
    source: str = "seed"


class JDUpdate(BaseModel):
    """修改已保存的 JD（只传要改的字段）。"""

    position_category: Optional[str] = None
    experience_level: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    skills: Optional[list] = None


class JDSchema(BaseModel):
    id: int
    position_category: str
    experience_level: str
    title: str
    content: str
    skills: Optional[Any] = None
    source: Optional[str] = Field(None, description="seed=内置示例；user=用户自己保存的")


# ---------------- 多岗位对比 ----------------
class CompareRequest(BaseModel):
    """把同一份简历投给多个岗位，横向对比哪个最合适。"""

    resume_text: str = Field(..., min_length=1)
    jd_ids: List[int] = Field(..., min_length=1, description="要比对的岗位 id 列表")
    with_llm: bool = Field(
        False,
        description="是否同时跑完整 AI 分析。默认 False：只用确定性 ATS 分排序，"
        "速度快且零成本；需要 AI 评分时再打开（会走缓存）。",
    )


class CompareItem(BaseModel):
    jd_id: int
    title: str
    position_category: str
    experience_level: str
    score: int = Field(..., description="ATS 多维加权分（确定性计算）")
    dimensions: List[ScoreDimension] = Field(default_factory=list)
    llm_score: Optional[int] = None
    cache_hit: bool = False


class CompareResponse(BaseModel):
    items: List[CompareItem] = Field(..., description="按 ATS 分从高到低排序")
    best_jd_id: Optional[int] = Field(None, description="得分最高的岗位 id")


# ---------------- 缓存统计 ----------------
class CacheStatsResponse(BaseModel):
    hits: int = Field(..., description="精确命中次数")
    semantic_hits: int = Field(0, description="语义近似命中次数")
    misses: int = Field(..., description="未命中次数")
    hit_rate: float = Field(..., description="总命中率（含语义）")
    memory_size: int
    total: int
    threshold: float = Field(0.95, description="语义命中阈值")
    semantic_enabled: bool = Field(True, description="语义缓存是否开启")
