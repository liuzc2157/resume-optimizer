"""FastAPI 入口：暴露简历优化 Agent 的两个方向 + 新增缓存/匹配/JD 库能力。

路由总览
--------
- POST /optimize        任务型：一次性返回 得分/分析/改写，命中缓存时 cache_hit=True
- POST /chat            对话型：多轮对话 Agent（带 Checkpointer 记忆），支持智能匹配
- POST /match           智能匹配：按岗位类别+经验等级返回候选岗位 Top-N（支持 use_rag 走向量检索）
- POST /rag/retrieve     RAG 向量检索：简历 -> 提炼 -> 召回最相似 Top-K 岗位 JD
- POST /vector/reindex   强制重建 JD 向量索引
- POST /classify        岗位自动分类：简历 -> 岗位类别/经验等级 + 置信度分布
- GET  /jds             岗位库列表
- POST /jds             新增岗位
- DELETE /jds/{id}      删除岗位
- GET  /cache/stats     缓存命中统计（hits / misses / 命中率）
- GET  /health          健康检查

RAG 向量库
----------
- 向量存于 JD 表的 embedding 列（JSON），rag_text 存清洗后的 JD 文本；
  进程内向量索引默认 FAISS(IndexFlatIP)，不可用时退化为纯 Python 余弦，零额外服务即可跑。
- 可切到托管向量库 Pinecone：设 VECTOR_BACKEND=pinecone + PINECONE_API_KEY，
  向量与轻量 metadata（类别/等级/标题）存 Pinecone，支持服务端按类别/等级过滤召回；
  索引维度在创建后不可改，维度不一致时 /vector/reindex 会给出清晰报错。
- 入库用「入库切片预处理 Prompt」精简 JD，查询用「检索查询预处理 Prompt」提炼简历。
"""

import logging
import os
import uuid
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

# 尽早加载 .env，保证 llm.py 读取 LLM_API_KEY 时环境变量已就绪
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

from .cache import cache_store  # noqa: E402
from .chat_graph import CHAT_SYSTEM_PROMPT, chat_agent  # noqa: E402
from .db import check_connection, init_db  # noqa: E402
from . import file_parser  # noqa: E402
from .graph import resume_agent  # noqa: E402  (需在 load_dotenv 之后导入)
from .matching import (  # noqa: E402
    add_jd,
    delete_jd,
    find_or_create_jd_by_content,
    list_jds,
    update_jd,
)
from .schemas import (  # noqa: E402
    CacheStatsResponse,
    ChatRequest,
    ChatResponse,
    ClassifyRequest,
    ClassifyResponse,
    CompareRequest,
    CompareResponse,
    JDSchema,
    JDCreate,
    JDUpdate,
    MatchRequest,
    MatchResponse,
    MatchedJDSchema,
    OptimizeRequest,
    OptimizeResponse,
    RagItem,
    RagRetrieveRequest,
    RagRetrieveResponse,
)
from . import rag, service  # noqa: E402

app = FastAPI(
    title="AI Resume Optimizer Agent",
    description="基于 LangGraph + MySQL + 缓存的简历优化 Agent（对话 / 任务双模式）",
    version="0.2.0",
)

# CORS：默认放行本地前端；生产环境通过 BACKEND_CORS_ORIGINS 注入域名（逗号分隔）
_default_origins = "http://localhost:3000,http://127.0.0.1:3000"
_origins = [o.strip() for o in os.getenv("BACKEND_CORS_ORIGINS", _default_origins).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/optimize", response_model=OptimizeResponse)
def optimize(req: OptimizeRequest) -> OptimizeResponse:
    """任务型：匹配岗位 → 查缓存 →（未命中）LLM 分析改写。"""
    try:
        result = service.optimize_resume(
            resume_text=req.resume_text,
            jd_text=req.jd_text,
            jd_id=req.jd_id,
            position_category=req.position_category,
            experience_level=req.experience_level,
            save_jd=req.save_jd,
            intensity=req.intensity or "enhance",
        )
    except ValueError as exc:  # 业务校验失败（如无可用 JD）
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001  LLM/DB 异常统一转 502
        logger.exception("Optimize 执行失败")
        raise HTTPException(status_code=502, detail=f"Optimize 执行失败：{exc}") from exc

    return OptimizeResponse(
        score=result["score"],
        llm_score=result.get("llm_score"),
        analysis=result["analysis"],
        rewrite=result["rewrite"],
        cache_hit=result["cache_hit"],
        cache_source=result["cache_source"],
        cache_similarity=result.get("cache_similarity", 0.0),
        matched_jd=result["matched_jd"],
        matches=result["matches"],
        breakdown=result.get("breakdown"),
        messages=[f"cache_hit={result['cache_hit']} source={result['cache_source']}"],
    )


@app.post("/match", response_model=MatchResponse)
def match(req: MatchRequest) -> MatchResponse:
    """智能匹配：use_rag=False 走关键词重叠+技能加权；use_rag=True 走 RAG 向量检索。"""
    try:
        result = service.match_resume(
            resume_text=req.resume_text,
            position_category=req.position_category,
            experience_level=req.experience_level,
            top_n=req.top_n,
            use_rag=req.use_rag,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Match 执行失败")
        raise HTTPException(status_code=502, detail=f"Match 执行失败：{exc}") from exc
    return MatchResponse(
        matches=[MatchedJDSchema(**m) for m in result["matches"]],
        mode=result["mode"],
        rag_query=result.get("rag_query"),
    )


@app.post("/rag/retrieve", response_model=RagRetrieveResponse)
def rag_retrieve_endpoint(req: RagRetrieveRequest) -> RagRetrieveResponse:
    """RAG 检索：简历 -> 提炼 -> 向量召回最相似的 Top-K 岗位 JD。

    返回的 items.rag_text 是 JD 经「入库切片预处理」后的精简文本，便于解释召回原因。
    """
    try:
        result = rag.rag_retrieve(req.resume_text, top_k=req.top_k, category=req.category, level=req.level)
        return RagRetrieveResponse(
            query_text=result["query_text"],
            items=[RagItem(**it) for it in result["items"]],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("RAG 检索失败")
        raise HTTPException(status_code=502, detail=f"RAG 检索失败：{exc}") from exc


@app.post("/vector/reindex")
def vector_reindex() -> Dict[str, Any]:
    """强制重建 JD 向量索引（新增/修改 JD 后手动刷新，或 embedder 配置变更后使用）。"""
    try:
        n = rag.reindex_jd_vectors()
        return {"indexed": n, "status": "ok"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("向量索引重建失败")
        raise HTTPException(status_code=502, detail=f"向量索引重建失败：{exc}") from exc


@app.post("/classify", response_model=ClassifyResponse)
def classify(req: ClassifyRequest) -> ClassifyResponse:
    """岗位自动分类：把简历归入岗位类别 + 经验等级，并返回可解释的置信度分布。

    纯关键词判定（与 JD 自动归类同源），不调 LLM、零成本；可在智能匹配前先预判
    候选人方向，或让用户点选分类条直接筛选匹配。
    """
    try:
        return ClassifyResponse(**service.classify_resume(req.resume_text))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Classify 执行失败")
        raise HTTPException(status_code=502, detail=f"Classify 执行失败：{exc}") from exc


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    """对话模式：多轮简历优化 Agent（支持智能匹配与缓存）。"""
    thread_id = req.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    init_state: Dict[str, Any] = {"messages": []}
    if not chat_agent.get_state(config).values:
        init_state["resume_text"] = ""
        init_state["jd_text"] = ""
        init_state["position_category"] = req.position_category or ""
        init_state["experience_level"] = req.experience_level or ""
        init_state["jd_id"] = req.jd_id or 0
        init_state["messages"].append({"role": "system", "content": CHAT_SYSTEM_PROMPT})
    else:
        # 续聊时把本轮指定的目标维度并入状态（若有）
        if req.position_category:
            init_state["position_category"] = req.position_category
        if req.experience_level:
            init_state["experience_level"] = req.experience_level
        if req.jd_id:
            init_state["jd_id"] = req.jd_id
    init_state["messages"].append({"role": "user", "content": req.message})

    try:
        state = chat_agent.invoke(init_state, config=config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Chat Agent 执行失败")
        raise HTTPException(status_code=502, detail=f"Chat Agent 执行失败：{exc}") from exc

    reply = next(
        (m["content"] for m in reversed(state["messages"]) if m["role"] == "assistant"),
        "（Agent 未返回内容，请重试）",
    )
    # 从 state 读取本轮是否命中缓存（由工具节点写入）
    cache_hit = bool(state.get("last_cache_hit", False))
    return ChatResponse(thread_id=thread_id, reply=reply, cache_hit=cache_hit)


# ---------------- JD 岗位库管理 ----------------
@app.get("/jds", response_model=list[JDSchema])
def get_jds(
    position_category: str | None = Query(None),
    experience_level: str | None = Query(None),
):
    """岗位库列表（可按维度过滤）。"""
    return [_jd_to_schema(j) for j in list_jds(position_category, experience_level)]


@app.post("/jds", response_model=JDSchema, status_code=201)
def create_jd(req: JDCreate) -> JDSchema:
    jd = add_jd(
        position_category=req.position_category,
        experience_level=req.experience_level,
        title=req.title,
        content=req.content,
        skills=req.skills,
        source=req.source or "seed",
    )
    try:
        rag.upsert_jd(jd.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("向量库同步新增 JD#%s 失败：%s", jd.id, exc)
    return JDSchema(
        id=jd.id,
        position_category=jd.position_category,
        experience_level=jd.experience_level,
        title=jd.title,
        content=jd.content,
        skills=jd.skills,
        source=jd.source,
    )


def _jd_to_schema(jd) -> JDSchema:
    return JDSchema(
        id=jd.id,
        position_category=jd.position_category,
        experience_level=jd.experience_level,
        title=jd.title,
        content=jd.content,
        skills=jd.skills,
        source=jd.source,
    )


@app.put("/jds/{jd_id}", response_model=JDSchema)
def modify_jd(jd_id: int, req: JDUpdate) -> JDSchema:
    """修改已保存的岗位（用户可对自动入库的 JD 做修正）。"""
    jd = update_jd(
        jd_id,
        position_category=req.position_category,
        experience_level=req.experience_level,
        title=req.title,
        content=req.content,
        skills=req.skills,
    )
    if jd is None:
        raise HTTPException(status_code=404, detail="岗位不存在")
    try:
        rag.upsert_jd(jd.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("向量库同步更新 JD#%s 失败：%s", jd.id, exc)
    return _jd_to_schema(jd)


@app.post("/jds/upload", response_model=JDSchema, status_code=201)
async def upload_jd(
    file: UploadFile = File(..., description="JD 文件，支持 txt/md/pdf/docx"),
    title: Optional[str] = Form(None, description="岗位名称，不填则自动取首行"),
    position_category: Optional[str] = Form(None, description="岗位类别，不填则自动推断"),
    experience_level: Optional[str] = Form(None, description="经验等级，不填则自动推断"),
) -> JDSchema:
    """上传 JD 文件 → 解析文本 → 保存进岗位库（source=user）。

    保存后即可复用：下次不用再传文件，智能匹配也能检索到它。
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件为空")
    try:
        content = file_parser.extract_text(file.filename or "", raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    content = (content or "").strip()
    if len(content) < 20:
        raise HTTPException(
            status_code=400,
            detail=f"解析出的文本过短（{len(content)} 字），请确认文件内容，或直接粘贴 JD 文本",
        )

    jd = find_or_create_jd_by_content(
        content,
        position_category=position_category,
        experience_level=experience_level,
        title=title,
        source="user",
    )
    try:
        rag.upsert_jd(jd.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("向量库同步上传 JD#%s 失败：%s", jd.id, exc)
    return _jd_to_schema(jd)


@app.post("/compare", response_model=CompareResponse)
def compare(req: CompareRequest) -> CompareResponse:
    """同一份简历 vs 多个岗位，横向对比「投哪个最合适」。

    默认只算确定性 ATS 分（不调 LLM，快且零成本）；需要 AI 评分时传 with_llm=true。
    """
    try:
        result = service.compare_resume(
            resume_text=req.resume_text,
            jd_ids=req.jd_ids,
            with_llm=req.with_llm,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Compare 执行失败")
        raise HTTPException(status_code=502, detail=f"Compare 执行失败：{exc}") from exc
    return CompareResponse(**result)


@app.delete("/jds/{jd_id}", status_code=204)
def remove_jd(jd_id: int) -> None:
    if not delete_jd(jd_id):
        raise HTTPException(status_code=404, detail="岗位不存在")
    try:
        rag.delete_jd(jd_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("向量库同步删除 JD#%s 失败：%s", jd_id, exc)


# ---------------- 缓存命中统计 ----------------
@app.get("/cache/stats", response_model=CacheStatsResponse)
def cache_stats() -> CacheStatsResponse:
    view = cache_store.stats()
    return CacheStatsResponse(
        hits=view.hits,
        semantic_hits=view.semantic_hits,
        misses=view.misses,
        hit_rate=view.hit_rate,
        memory_size=view.memory_size,
        total=view.total,
        threshold=view.threshold,
        semantic_enabled=view.semantic_enabled,
    )


@app.get("/health")
def health() -> Dict[str, Any]:
    """健康检查 + DB 探活，供 CloudBase 等平台探活使用。"""
    return {"status": "ok", "db": "connected" if check_connection() else "unavailable"}


@app.on_event("startup")
def on_startup() -> None:
    """启动时初始化数据库（建表 + 缓存统计单行）。"""
    try:
        init_db()
        logger.info("Resume Optimizer Agent 启动完成，数据库已就绪")
    except Exception as exc:  # noqa: BLE001
        logger.warning("数据库初始化失败（应用仍可启动，相关接口会报错）：%s", exc)
