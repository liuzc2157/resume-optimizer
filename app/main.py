"""FastAPI 入口：暴露简历优化的两个方向 + 认证/配额/RAG 等能力。

路由总览
--------
- POST /auth/register   注册（需同意隐私政策）
- POST /auth/login      登录，返回 JWT
- GET  /users/me        个人信息 + 配额 + 用量
- DELETE /users/me      注销并清除全部个人数据
- POST /optimize        任务型：一次性返回 得分/分析/改写，命中缓存时 cache_hit=True
- POST /chat            对话型：多轮对话（DB 持久化记忆），支持智能匹配
- POST /match           智能匹配：按岗位类别+经验等级返回候选岗位 Top-N（支持 use_rag）
- POST /rag/retrieve     RAG 向量检索：简历 -> 提炼 -> 召回最相似 Top-K 岗位 JD
- POST /vector/reindex   强制重建 JD 向量索引（仅管理员）
- POST /classify        岗位自动分类：简历 -> 岗位类别/经验等级 + 置信度分布
- GET  /jds             岗位库列表（种子 + 本人）
- POST /jds             新增岗位（归属当前用户）
- PUT  /jds/{id}        修改岗位
- DELETE /jds/{id}      删除岗位
- POST /jds/upload      上传 JD 文件（txt/md/pdf/docx）
- POST /resume/parse    解析简历文件为纯文本（txt/md/pdf/docx，不计配额）
- GET/POST /resumes     简历库：列出 / 新建（每用户最多 3 份）
- PUT/DELETE /resumes/{id} 覆盖 / 删除已保存简历
- POST /compare         同一份简历 vs 多个岗位横向对比
- GET  /cache/stats     缓存命中统计
- GET  /health          健康检查（公开）

鉴权与配额
----------
- 除 /auth/* 与 /health 外，全部业务接口需要 Authorization: Bearer <token>。
- 所有业务接口受「每分钟限流 + 每日配额」保护（见 rate_limit.py），
  防刷接口把 LLM 费用打爆。
- 每次调用写入 usage_log（token 用量 + 缓存命中），供配额/审计/未来计费使用。
"""

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

# 尽早加载 .env，保证 llm.py 读取 LLM_API_KEY 时环境变量已就绪
load_dotenv()

# 结构化日志：LOG_FORMAT=json 时输出 JSON 行（便于日志平台采集），默认人类可读。
class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": logging.Formatter.formatTime(self, record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # 保留异常堆栈：JSON 模式下 logger.exception 的 traceback 不能丢
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_formatter = _JsonFormatter() if os.getenv("LOG_FORMAT", "").strip().lower() == "json" else None
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
if _formatter is not None:
    for handler in logging.getLogger().handlers:
        handler.setFormatter(_formatter)

logger = logging.getLogger(__name__)

from .auth import router as auth_router  # noqa: E402
from .cache import cache_store  # noqa: E402
from .chat_graph import CHAT_SYSTEM_PROMPT, chat_agent  # noqa: E402
from .db import check_connection, db_session, init_db  # noqa: E402
from . import file_parser  # noqa: E402
from .llm import LLMConnectionError  # noqa: E402
from .graph import resume_agent  # noqa: E402  (需在 load_dotenv 之后导入)
from .matching import (  # noqa: E402
    add_jd,
    delete_jd,
    find_or_create_jd_by_content,
    list_jds,
    update_jd,
)
from .rate_limit import consume_quota, enforce_rate_limit  # noqa: E402
from .resumes import router as resumes_router  # noqa: E402
from .models import SavedResume  # noqa: E402
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
    ParseResumeResponse,
    MatchedJDSchema,
    OptimizeRequest,
    OptimizeResponse,
    RagItem,
    RagRetrieveRequest,
    RagRetrieveResponse,
)
from .security import is_admin_user  # noqa: E402
from . import rag, service  # noqa: E402
from .usage import record_usage, usage_scope  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时初始化数据库（建表 + 缓存统计单行 + 种子 JD）。"""
    try:
        init_db()
        logger.info("简遇（Briefly）启动完成，数据库已就绪")
    except Exception as exc:  # noqa: BLE001
        logger.warning("数据库初始化失败（应用仍可启动，相关接口会报错）：%s", exc)
    yield


app = FastAPI(
    title="简遇 Briefly · AI 简历优化",
    description="基于 LangGraph + MySQL + 缓存 + 账号配额的简历优化平台（对话 / 任务双模式）",
    version="0.3.0",
    lifespan=lifespan,
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

app.include_router(auth_router)
app.include_router(resumes_router)

@app.exception_handler(LLMConnectionError)
async def llm_connection_error_handler(_request, exc: LLMConnectionError):
    """LLM 网络/连接失败 -> 502 + 友好文案（不把 httpx 原始报错抛给前端）。"""
    logger.warning("LLM 连接失败：%s", exc)
    from fastapi.responses import JSONResponse  # noqa: PLC0415

    return JSONResponse(status_code=502, content={"detail": str(exc)})



@app.get("/health")
def health() -> Dict[str, Any]:
    """健康检查 + DB 探活（公开，供平台探活使用）。"""
    return {"status": "ok", "db": "connected" if check_connection() else "unavailable"}


@app.post("/optimize", response_model=OptimizeResponse)
def optimize(
    req: OptimizeRequest,
    user=Depends(enforce_rate_limit),
    usage: dict = Depends(usage_scope),
    s: Session = Depends(db_session),
) -> OptimizeResponse:
    """任务型：匹配岗位 → 查缓存 →（未命中）LLM 分析改写。"""
    consume_quota(s, user, "/optimize")  # 原子占配额，并发不会超额
    try:
        result = service.optimize_resume(
            resume_text=req.resume_text,
            jd_text=req.jd_text,
            jd_id=req.jd_id,
            position_category=req.position_category,
            experience_level=req.experience_level,
            save_jd=req.save_jd,
            intensity=req.intensity or "enhance",
            user_id=user.id,
            usage_bucket=usage,
        )
    except ValueError as exc:  # 业务校验失败（如无可用 JD）
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001  LLM/DB 异常统一转 502
        logger.exception("Optimize 执行失败")
        raise HTTPException(status_code=502, detail=f"Optimize 执行失败：{exc}") from exc

    record_usage(s, user.id, "/optimize", cache_hit=result["cache_hit"], bucket=usage)
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
        messages=result.get("messages", []),
    )


@app.post("/match", response_model=MatchResponse)
def match(
    req: MatchRequest,
    user=Depends(enforce_rate_limit),
    usage: dict = Depends(usage_scope),
    s: Session = Depends(db_session),
) -> MatchResponse:
    """智能匹配：use_rag=False 走关键词重叠+技能加权；use_rag=True 走 RAG 向量检索。"""
    if req.use_rag:
        # 只有 RAG 分支会调 LLM（提炼检索词），才占用配额；
        # 纯关键词匹配是本地零成本计算，不占额度。
        consume_quota(s, user, "/match")
    try:
        result = service.match_resume(
            resume_text=req.resume_text,
            position_category=req.position_category,
            experience_level=req.experience_level,
            top_n=req.top_n,
            use_rag=req.use_rag,
            user_id=user.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Match 执行失败")
        raise HTTPException(status_code=502, detail=f"Match 执行失败：{exc}") from exc
    # 只有走 RAG（会调 LLM 提炼检索词）才算一次配额消耗；
    # 纯关键词匹配是零成本计算，用单独标签记账，不计入 quota。
    match_endpoint = "/match" if req.use_rag else "/match/keyword"
    record_usage(s, user.id, match_endpoint, cache_hit=False, bucket=usage)
    return MatchResponse(
        matches=[MatchedJDSchema(**m) for m in result["matches"]],
        mode=result["mode"],
        rag_query=result.get("rag_query"),
    )


@app.post("/rag/retrieve", response_model=RagRetrieveResponse)
def rag_retrieve_endpoint(
    req: RagRetrieveRequest,
    user=Depends(enforce_rate_limit),
    usage: dict = Depends(usage_scope),
    s: Session = Depends(db_session),
) -> RagRetrieveResponse:
    """RAG 检索：简历 -> 提炼 -> 向量召回最相似的 Top-K 岗位 JD。"""
    consume_quota(s, user, "/rag/retrieve")  # 内部会调 LLM 提炼检索词
    try:
        result = rag.rag_retrieve(
            req.resume_text, top_k=req.top_k, category=req.category, level=req.level, user_id=user.id
        )
        # 该接口内部会调用 LLM 提炼简历查询词，必须计入配额并写用量日志
        record_usage(s, user.id, "/rag/retrieve", cache_hit=False, bucket=usage)
        return RagRetrieveResponse(
            query_text=result["query_text"],
            items=[RagItem(**it) for it in result["items"]],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("RAG 检索失败")
        raise HTTPException(status_code=502, detail=f"RAG 检索失败：{exc}") from exc


@app.post("/vector/reindex")
def vector_reindex(
    user=Depends(enforce_rate_limit),
    usage: dict = Depends(usage_scope),
    s: Session = Depends(db_session),
) -> Dict[str, Any]:
    """强制重建 JD 向量索引（仅管理员）。"""
    if not is_admin_user(user):
        raise HTTPException(status_code=403, detail="仅管理员可执行重建索引")
    try:
        n = rag.reindex_jd_vectors()
        record_usage(s, user.id, "/vector/reindex", cache_hit=False, bucket=usage)
        return {"indexed": n, "status": "ok"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("向量索引重建失败")
        raise HTTPException(status_code=502, detail=f"向量索引重建失败：{exc}") from exc


@app.post("/classify", response_model=ClassifyResponse)
def classify(
    req: ClassifyRequest,
    user=Depends(enforce_rate_limit),
    usage: dict = Depends(usage_scope),
    s: Session = Depends(db_session),
) -> ClassifyResponse:
    """岗位自动分类：把简历归入岗位类别 + 经验等级，并返回可解释的置信度分布。"""
    try:
        return ClassifyResponse(**service.classify_resume(req.resume_text))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Classify 执行失败")
        raise HTTPException(status_code=502, detail=f"Classify 执行失败：{exc}") from exc


@app.post("/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    user=Depends(enforce_rate_limit),
    usage: dict = Depends(usage_scope),
    s: Session = Depends(db_session),
) -> ChatResponse:
    """对话模式：多轮简历优化（DB 持久化记忆，按用户隔离会话）。

    thread_id 以「用户id:会话id」命名空间存储，保证不同用户的会话互不可见；
    返回给客户端的仍是原始 thread_id（续聊时携带即可）。
    """
    consume_quota(s, user, "/chat")  # 原子占配额，并发不会超额
    raw_thread = req.thread_id or str(uuid.uuid4())
    thread_id = f"{user.id}:{raw_thread}"
    config = {"configurable": {"thread_id": thread_id, "user_id": user.id, "usage_bucket": usage}}

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
    # 从简历库载入指定简历（可选）：对话直接基于该简历进行，无需用户再粘贴
    if req.resume_id:
        saved = s.scalar(
            select(SavedResume).where(
                SavedResume.id == req.resume_id,
                SavedResume.user_id == user.id,
            )
        )
        if saved is None:
            raise HTTPException(status_code=404, detail="简历不存在或无权访问")
        init_state["resume_text"] = saved.content or ""
        init_state["messages"].append(
            {
                "role": "system",
                "content": (
                    f"已从用户简历库载入简历《{saved.name or '未命名简历'}》，内容如下：\n"
                    f"---\n{saved.content}\n---\n"
                    "后续用户将基于这份简历提问或要求优化，无需再向用户索要简历全文。"
                ),
            }
        )
    init_state["messages"].append({"role": "user", "content": req.message})

    try:
        state = chat_agent.invoke(init_state, config=config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Chat 执行失败")
        raise HTTPException(status_code=502, detail=f"Chat 执行失败：{exc}") from exc

    reply = next(
        (m["content"] for m in reversed(state["messages"]) if m["role"] == "assistant"),
        "（未返回内容，请重试）",
    )
    cache_hit = bool(state.get("last_cache_hit", False))
    record_usage(s, user.id, "/chat", cache_hit=cache_hit, bucket=usage)
    return ChatResponse(thread_id=raw_thread, reply=reply, cache_hit=cache_hit)


# ---------------- JD 岗位库管理 ----------------
@app.get("/jds", response_model=list[JDSchema])
def get_jds(
    position_category: str | None = Query(None),
    experience_level: str | None = Query(None),
    user=Depends(enforce_rate_limit),
    s: Session = Depends(db_session),
):
    """岗位库列表（种子岗位 + 当前用户自己保存的岗位）。"""
    return [_jd_to_schema(j) for j in list_jds(position_category, experience_level, user_id=user.id)]


@app.post("/jds", response_model=JDSchema, status_code=201)
def create_jd(
    req: JDCreate,
    user=Depends(enforce_rate_limit),
    s: Session = Depends(db_session),
) -> JDSchema:
    jd = add_jd(
        position_category=req.position_category,
        experience_level=req.experience_level,
        title=req.title,
        content=req.content,
        skills=req.skills,
        source=req.source or "seed",
        user_id=user.id,
    )
    try:
        rag.upsert_jd(jd.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("向量库同步新增 JD#%s 失败：%s", jd.id, exc)
    return _jd_to_schema(jd)


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
def modify_jd(
    jd_id: int,
    req: JDUpdate,
    user=Depends(enforce_rate_limit),
    s: Session = Depends(db_session),
) -> JDSchema:
    """修改已保存的岗位（仅本人创建的岗位可改；种子岗位只读）。"""
    from .matching import get_jd

    if (jd := get_jd(jd_id, user_id=user.id)) is None or jd.user_id is None:
        raise HTTPException(status_code=403, detail="只能修改自己创建的岗位（种子岗位只读）")
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
    user=Depends(enforce_rate_limit),
    s: Session = Depends(db_session),
) -> JDSchema:
    """上传 JD 文件 → 解析文本 → 保存进当前用户的岗位库。"""
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
        user_id=user.id,
    )
    try:
        rag.upsert_jd(jd.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("向量库同步上传 JD#%s 失败：%s", jd.id, exc)
    return _jd_to_schema(jd)



@app.post("/resume/parse", response_model=ParseResumeResponse)
async def parse_resume(
    file: UploadFile = File(..., description="简历文件，支持 txt/md/pdf/docx"),
    user=Depends(enforce_rate_limit),
) -> ParseResumeResponse:
    """解析简历文件为纯文本（本地解析，不调用 LLM、不消耗配额）。"""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件为空")
    try:
        content = file_parser.extract_text(file.filename or "", raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    content = (content or "").strip()
    if len(content) < 10:
        raise HTTPException(
            status_code=400,
            detail="解析出的文本过短，请确认文件内容，或直接粘贴简历文本",
        )
    return ParseResumeResponse(filename=file.filename or "", text=content)

@app.post("/compare", response_model=CompareResponse)
def compare(
    req: CompareRequest,
    user=Depends(enforce_rate_limit),
    usage: dict = Depends(usage_scope),
    s: Session = Depends(db_session),
) -> CompareResponse:
    """同一份简历 vs 多个岗位，横向对比「投哪个最合适」。"""
    consume_quota(s, user, "/compare")  # 原子占配额，并发不会超额
    try:
        result = service.compare_resume(
            resume_text=req.resume_text,
            jd_ids=req.jd_ids,
            with_llm=req.with_llm,
            user_id=user.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Compare 执行失败")
        raise HTTPException(status_code=502, detail=f"Compare 执行失败：{exc}") from exc
    record_usage(s, user.id, "/compare", cache_hit=False, bucket=usage)
    return CompareResponse(**result)


@app.delete("/jds/{jd_id}", status_code=204)
def remove_jd(
    jd_id: int,
    user=Depends(enforce_rate_limit),
    s: Session = Depends(db_session),
) -> None:
    """删除岗位（仅本人创建的岗位可删；种子岗位不可删）。"""
    from .matching import get_jd

    if (jd := get_jd(jd_id, user_id=user.id)) is None or jd.user_id is None:
        raise HTTPException(status_code=403, detail="只能删除自己创建的岗位（种子岗位只读）")
    delete_jd(jd_id)
    try:
        rag.delete_jd(jd_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("向量库同步删除 JD#%s 失败：%s", jd_id, exc)


# ---------------- 缓存命中统计 ----------------
@app.get("/cache/stats", response_model=CacheStatsResponse)
def cache_stats(
    user=Depends(enforce_rate_limit),
    s: Session = Depends(db_session),
) -> CacheStatsResponse:
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
