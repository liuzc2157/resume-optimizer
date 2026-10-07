"""RAG 检索子系统：向量库 + 入库/查询两段预处理 Prompt。

设计
----
- 入库阶段（JD 入库 / 建索引时）：用「入库切片预处理 Prompt」把 JD 原始文本精简成
  适合向量检索的片段（去冗余、留技术关键词），再向量化存入向量库。
- 查询阶段（用户粘贴简历时）：用「检索查询预处理 Prompt」提炼简历核心技术能力，
  生成 query 向量，去向量库做余弦检索，召回最相似的 Top-K 个 JD。

向量库实现（可插拔后端，由 VECTOR_BACKEND 切换）
-----------------------------------------------
- faiss（默认）：进程内向量索引（FAISS IndexFlatIP 优先；不可用时退化为纯 Python 余弦），
  向量持久化在 JD 表的 embedding 列（JSON），rag_text 存清洗后的 JD 文本。
  无需额外服务、本地零依赖即可跑，云端重部署（Railway/CloudBase）也不丢索引。
- pinecone：托管 serverless 向量库（VECTOR_BACKEND=pinecone）。向量 + 轻量
  metadata（position_category / experience_level / title）存在 Pinecone，
  正文 rag_text 仍回 JD 表查询。支持服务端 metadata 过滤（按岗位类别/等级召回）。
  需 PINECONE_API_KEY；索引维度在创建后不可更改，维度不一致时给出清晰报错。

切换真实向量库（Milvus / pgvector / Qdrant）时，只需新增一个实现
upsert / delete / clear / search / count 的后端类，并在 build_or_load_store
里注册，对外 rag_retrieve / upsert_jd / delete_jd 签名不变。

依赖
----
- 向量：复用 app.embedding.embed()（本地哈希 或 OpenAI 兼容 API）。
- 文本清洗：复用 app.llm.call_llm()，MOCK_LLM=1 或调用失败均回退原始文本。
- pinecone 后端需额外安装：pip install "pinecone>=3.0.0"
"""

import json
import logging
import os
from typing import List, Optional

from sqlalchemy import select

from . import embedding, llm
from .db import get_session
from .models import JD

logger = logging.getLogger(__name__)

# ---------------- 两段预处理 Prompt（用户指定） ----------------
JD_CLEAN_SYSTEM = (
    "你是文本清洗助手，用于向量化入库预处理。\n"
    "请提取这份岗位JD的核心内容，精简为适合向量检索的片段：\n"
    "1. 保留岗位核心工作职责、硬性技术要求、技术栈关键词；\n"
    "2. 删除无关福利、地点、公司介绍等冗余文字；\n"
    "3. 不要总结润色过度，保留原始技术术语；\n"
    "4. 输出纯文本，不要标题、不要列表、不要多余解释。"
)
JD_CLEAN_USER_TMPL = "JD原文：\n{raw_jd_text}"

RESUME_QUERY_SYSTEM = (
    "你是查询文本提炼助手，用于向量检索。\n"
    "从下面简历提取核心技术能力、项目栈，生成简短检索文本，用来在向量库匹配岗位JD。\n"
    "只保留技术关键词、项目能力，删掉个人信息、教育无关描述，输出一段精简文本。"
)
RESUME_QUERY_USER_TMPL = "简历原文：\n{resume_text}"


def clean_jd_for_index(raw_jd: str) -> str:
    """入库切片预处理：精简 JD 文本，保留技术核心，剔除冗余。

    失败 / 离线（MOCK）时回退原始文本，保证索引永不因清洗失败而缺数据。
    """
    raw = (raw_jd or "").strip()
    if not raw:
        return raw
    if llm.MOCK_LLM:  # 离线验证模式：直接用原文向量化，跳过 LLM 清洗
        return raw
    try:
        cleaned = llm.call_llm(JD_CLEAN_SYSTEM, JD_CLEAN_USER_TMPL.format(raw_jd_text=raw))
        cleaned = (cleaned or "").strip()
        if len(cleaned) >= 10:
            return cleaned
    except Exception as exc:  # noqa: BLE001
        logger.warning("[rag] JD 入库清洗失败，回退原文：%s", exc)
    return raw


def extract_resume_query(resume: str) -> str:
    """检索查询预处理：从简历提炼核心技术能力，生成 query 文本。"""
    resume = (resume or "").strip()
    if not resume:
        return resume
    if llm.MOCK_LLM:  # 离线验证模式：直接用原文向量化，跳过 LLM 提炼
        return resume
    try:
        q = llm.call_llm(RESUME_QUERY_SYSTEM, RESUME_QUERY_USER_TMPL.format(resume_text=resume))
        q = (q or "").strip()
        if len(q) >= 5:
            return q
    except Exception as exc:  # noqa: BLE001
        logger.warning("[rag] 简历查询提炼失败，回退原文：%s", exc)
    return resume


# ---------------- metadata 过滤 ----------------
def _match_filter(meta: dict, filter_dict: Optional[dict]) -> bool:
    """判断 meta 是否满足 filter_dict（仅支持 $eq，与 Pinecone 语法对齐）。"""
    if not filter_dict:
        return True
    for key, cond in filter_dict.items():
        val = meta.get(key)
        if isinstance(cond, dict) and "$eq" in cond:
            if val != cond["$eq"]:
                return False
        elif val != cond:
            return False
    return True


def _build_filter(category: Optional[str], level: Optional[str]) -> Optional[dict]:
    f: dict = {}
    if category:
        f["position_category"] = {"$eq": category}
    if level:
        f["experience_level"] = {"$eq": level}
    return f or None


# ---------------- 向量索引：FAISS（默认，零依赖） ----------------
class JDVectorStore:
    """JD 向量索引：FAISS(IndexFlatIP) 优先，否则纯 Python 余弦。

    向量均 L2 归一化，故内积 == 余弦相似度。
    """

    backend_name = "faiss"

    def __init__(self, dims: int) -> None:
        self.dims = dims
        self.ids: List[int] = []
        self.metas: List[dict] = []  # {position_category, experience_level, title}
        self.vectors: List[List[float]] = []
        self._index = None
        self._init_index()

    def _init_index(self) -> None:
        try:
            import faiss  # type: ignore

            self._index = faiss.IndexFlatIP(self.dims)
            logger.info("[rag] 使用 FAISS IndexFlatIP 向量索引 (dim=%d)", self.dims)
        except Exception:  # noqa: BLE001
            self._index = None
            logger.info("[rag] FAISS 不可用，使用纯 Python 余弦索引 (dim=%d)", self.dims)

    def upsert(self, jd_id: int, meta: dict, vector: List[float]) -> None:
        """新增或覆盖一条 JD 向量（build 遍历时每条只 add 一次，不会重复）。"""
        if jd_id in self.ids:
            return
        self.ids.append(jd_id)
        self.metas.append(meta)
        self.vectors.append(vector)
        if self._index is not None:
            import numpy as np  # 延迟导入，避免无 faiss 时也必须装 numpy

            self._index.add(np.array([vector], dtype="float32"))

    def search(self, query_vec: List[float], top_k: int, filter_dict: Optional[dict] = None) -> List[tuple]:
        """返回 [(jd_id, meta, score)]，按 score 降序，取前 top_k。"""
        if not self.ids:
            return []
        if self._index is not None:
            import numpy as np  # 延迟导入

            q = np.array([query_vec], dtype="float32")
            k = max(top_k, 50) if filter_dict else top_k
            k = min(k, len(self.ids))
            scores, idxs = self._index.search(q, k)
            results = [
                (self.ids[i], self.metas[i], float(s))
                for s, i in zip(scores[0], idxs[0])
                if i >= 0
            ]
        else:
            # 纯 Python 余弦（复用 embedding.cosine）
            results = [
                (jid, meta, embedding.cosine(query_vec, vec))
                for jid, meta, vec in zip(self.ids, self.metas, self.vectors)
            ]
        if filter_dict:
            results = [r for r in results if _match_filter(r[1], filter_dict)]
        results.sort(key=lambda x: x[2], reverse=True)
        return results[:top_k]

    def count(self) -> int:
        return len(self.ids)

    def __contains__(self, jd_id: int) -> bool:
        return jd_id in self.ids


# ---------------- 向量索引：Pinecone（托管 serverless） ----------------
class PineconeVectorStore:
    """Pinecone 后端：向量 + 轻量 metadata 存在 Pinecone，正文回 JD 表查询。

    支持服务端 metadata 过滤（按岗位类别 / 经验等级召回）。
    """

    backend_name = "pinecone"

    def __init__(
        self,
        dims: int,
        index_name: str,
        api_key: str,
        cloud: str = "aws",
        region: str = "us-east-1",
    ) -> None:
        self.dims = dims
        self.index_name = index_name
        try:
            from pinecone import Pinecone, ServerlessSpec  # type: ignore
        except ImportError:  # noqa: BLE001
            raise RuntimeError("未安装 pinecone SDK，请先 `pip install \"pinecone>=3.0.0\"`")

        self._pc = Pinecone(api_key=api_key)
        names = [i.name for i in self._pc.list_indexes()]
        if index_name not in names:
            logger.info("[rag] 创建 Pinecone 索引 %s (dim=%d, %s/%s)", index_name, dims, cloud, region)
            self._pc.create_index(
                name=index_name,
                dimension=dims,
                metric="cosine",
                spec=ServerlessSpec(cloud=cloud, region=region),
            )
        self._index = self._pc.Index(index_name)

        stats = self._index.describe_index_stats() or {}
        existing = stats.get("dimension")
        if existing and int(existing) != dims:
            raise ValueError(
                f"Pinecone 索引 '{index_name}' 维度为 {existing}，与当前 embedding 维度 {dims} 不符。"
                f"维度在创建后不可更改，请删除该索引后重新运行 /vector/reindex。"
            )

    def upsert(self, jd_id: int, meta: dict, vector: List[float]) -> None:
        pc_meta = {
            "position_category": meta.get("position_category") or "",
            "experience_level": meta.get("experience_level") or "",
            "title": meta.get("title") or "",
        }
        self._index.upsert([(str(jd_id), vector, pc_meta)])

    def delete(self, jd_id: int) -> None:
        self._index.delete(ids=[str(jd_id)])

    def clear(self) -> None:
        self._index.delete(delete_all=True)

    def search(self, query_vec: List[float], top_k: int, filter_dict: Optional[dict] = None) -> List[tuple]:
        k = max(top_k, 50)
        resp = self._index.query(
            vector=query_vec, top_k=k, filter=filter_dict, include_metadata=True
        )
        out: List[tuple] = []
        for m in resp.get("matches", []):
            md = m.get("metadata", {}) or {}
            out.append(
                (
                    int(m["id"]),
                    {
                        "position_category": md.get("position_category") or None,
                        "experience_level": md.get("experience_level") or None,
                        "title": md.get("title") or "",
                    },
                    float(m["score"]),
                )
            )
        if filter_dict:
            out = [r for r in out if _match_filter(r[1], filter_dict)]
        out.sort(key=lambda x: x[2], reverse=True)
        return out[:top_k]

    def count(self) -> int:
        stats = self._index.describe_index_stats() or {}
        return stats.get("total_vector_count", 0)

    def __contains__(self, jd_id: int) -> bool:
        # 远程即真相源，不维护本地 id 集合
        return False


# ---------------- 索引构建 / 单例管理 ----------------
def _embedder_signature() -> str:
    """向量维度 + provider 指纹，用于检测 embedder 变更（维度变了必须重建索引）。"""
    emb = embedding.get_embedder()
    dim = int(getattr(emb, "dim", None) or os.getenv("EMBEDDING_DIM", "256"))
    provider = os.getenv("EMBEDDING_PROVIDER", "local")
    model = os.getenv("EMBEDDING_MODEL", "")
    return f"{provider}:{model}:{dim}"


def _create_pinecone_store(dim: int) -> PineconeVectorStore:
    api_key = os.getenv("PINECONE_API_KEY")
    if not api_key:
        raise RuntimeError("VECTOR_BACKEND=pinecone 但缺少 PINECONE_API_KEY 环境变量")
    name = os.getenv("PINECONE_INDEX", "resume-jds")
    cloud = os.getenv("PINECONE_CLOUD", "aws")
    region = os.getenv("PINECONE_REGION", "us-east-1")
    return PineconeVectorStore(dim, name, api_key, cloud, region)


def _compute_jd_embedding(jd: JD) -> None:
    """为单条 JD 计算 rag_text + embedding 并写回（幂等）。"""
    rag_text = clean_jd_for_index(jd.content)
    vec = embedding.embed(rag_text)
    jd.rag_text = rag_text
    jd.embedding = json.dumps(vec, ensure_ascii=False)


_store: Optional[object] = None
_store_token: Optional[tuple] = None
_store_kind: Optional[str] = None


def build_or_load_store(force: bool = False):
    """构建 / 加载 JD 向量索引（进程内单例，按指纹自动失效重建）。

    - 后端由 VECTOR_BACKEND 决定：faiss（默认）或 pinecone。
    - 全量重建触发：force=True、后端切换、向量库指纹变化（provider/维度）、
      JD 数量或最新更新时间变化；
    - 增量补齐：仅对缺失 embedding 的 JD 计算（新增 JD 自动纳入，无需全量重算）。
    - pinecone 后端：远程即真相源，非强制时直接复用，不需遍历补齐。
    """
    global _store, _store_token, _store_kind
    backend = os.getenv("VECTOR_BACKEND", "faiss").lower()
    sig = _embedder_signature()
    dim = int(sig.split(":")[-1])
    old_sig = _store_token[0] if _store_token else None

    s = get_session()
    try:
        jds = list(s.execute(select(JD).order_by(JD.id)).scalars().all())
        count = len(jds)
        max_updated = max((jd.updated_at for jd in jds if jd.updated_at), default=None)
        token = (sig, count, str(max_updated), backend)

        need_new = _store is None or _store_kind != backend
        rebuild = force or need_new or _store_token != token

        if rebuild:
            if backend == "pinecone":
                _store = _create_pinecone_store(dim)
                if force:
                    _store.clear()
            else:
                _store = JDVectorStore(dims=dim)
            _store_kind = backend
            _store_token = token

        # Pinecone 远程已是真相源，非强制时无需遍历补齐
        if backend == "pinecone" and not (rebuild or force):
            return _store

        embedder_changed = bool(old_sig and old_sig != sig)
        recompute_all = rebuild and (force or embedder_changed)
        for jd in jds:
            if (not rebuild) and jd.id in _store:  # 已在内存索引，跳过
                continue
            if recompute_all or not jd.embedding:
                _compute_jd_embedding(jd)
            vec = json.loads(jd.embedding)
            meta = {
                "position_category": jd.position_category,
                "experience_level": jd.experience_level,
                "title": jd.title,
            }
            _store.upsert(jd.id, meta, vec)
        s.commit()
        logger.info(
            "[rag] 向量索引就绪：%d 条 JD（backend=%s, rebuild=%s）", count, backend, rebuild
        )
        return _store
    finally:
        s.close()


def rag_retrieve(resume_text: str, top_k: int = 5, category: Optional[str] = None,
                 level: Optional[str] = None) -> dict:
    """RAG 检索：简历 -> 提炼 -> 向量 -> 召回 Top-K 相似 JD（支持类别/等级过滤）。

    返回 {query_text, items:[{jd_id, title, position_category, experience_level,
    similarity, rag_text}]}，items 字段与 MatchedJDSchema 兼容（可直接喂 /match 前端）。
    rag_text 统一从 JD 表按 jd_id 取（Pinecone 不存正文，避免 metadata 超限）。
    """
    store = build_or_load_store()
    query_text = extract_resume_query(resume_text)
    qvec = embedding.embed(query_text)
    filt = _build_filter(category, level)
    raw = store.search(qvec, top_k, filt)

    jd_ids = [jid for jid, _, _ in raw]
    rag_texts: dict = {}
    if jd_ids:
        s = get_session()
        try:
            rows = s.execute(select(JD.id, JD.rag_text, JD.content).where(JD.id.in_(jd_ids))).all()
            for rid, rtext, rcontent in rows:
                rag_texts[rid] = rtext or rcontent or ""
        finally:
            s.close()

    items = []
    for jd_id, meta, score in raw:
        items.append(
            {
                "jd_id": jd_id,
                "title": meta["title"],
                "position_category": meta["position_category"],
                "experience_level": meta["experience_level"],
                "similarity": round(float(score), 4),
                "rag_text": rag_texts.get(jd_id, ""),
            }
        )
    return {"query_text": query_text, "items": items}


def upsert_jd(jd_id: int) -> None:
    """JD 新增/修改后，把该条同步进向量库（增量，不重建全量索引）。

    - pinecone：直接 upsert 到远程索引；
    - faiss：重算 embedding 写回 DB，并标记下次检索重建索引以纳入新 JD。
    """
    s = get_session()
    try:
        jd = s.get(JD, jd_id)
        if jd is None:
            logger.warning("[rag] upsert_jd 找不到 JD#%s", jd_id)
            return
        _compute_jd_embedding(jd)
        s.commit()
        vec = json.loads(jd.embedding)
        meta = {
            "position_category": jd.position_category,
            "experience_level": jd.experience_level,
            "title": jd.title,
        }
        store = build_or_load_store()
        if getattr(store, "backend_name", "faiss") == "pinecone":
            try:
                store.upsert(jd.id, meta, vec)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[rag] Pinecone upsert JD#%s 失败：%s", jd_id, exc)
        else:
            global _store_token
            _store_token = None  # 使 FAISS 下次检索重建纳入新 JD
    finally:
        s.close()


def delete_jd(jd_id: int) -> None:
    """JD 删除后，从向量库移除该条。"""
    store = build_or_load_store()
    if getattr(store, "backend_name", "faiss") == "pinecone":
        try:
            store.delete(jd_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[rag] Pinecone 删除 JD#%s 失败：%s", jd_id, exc)
    else:
        global _store_token
        _store_token = None


def reindex_jd_vectors() -> int:
    """强制重建 JD 向量索引，返回索引条数。"""
    store = build_or_load_store(force=True)
    return store.count()
