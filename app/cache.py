"""三级缓存层：进程内 LRU → MySQL 精确键 → MySQL 语义近似。

设计目标
--------
1. 同一进程内重复请求走内存，零 DB 开销；
2. 跨进程重启（如云端重新拉起）仍可从 MySQL 命中；
3. **精确键命中率在真实场景通常很低**（用户很少逐字重复提交），
   因此增加「语义近似」层：简历改了几个字、调了语序也能命中；
4. 每次命中/未命中都累计计数，通过 /cache/stats 暴露命中率与命中类型。

分级与防误命中（关键）
----------------------
    L1 精确键  ：md5(版本 | 归一化简历 | 岗位引用)   —— 免费且绝对安全
    L2 语义近似：余弦相似度 >= 阈值(默认 0.95)
    L3 LLM     ：真正调用模型

语义缓存最大的风险是**误命中**（把 A 简历的分析错给 B 简历）。本项目用三重约束压住：
  a) **分区检索**：只在**同一个岗位引用 jd_ref 内部**比较，绝不跨岗位；
  b) **高阈值**：默认 0.95（行业常用 0.90~0.95，取保守值）；
  c) **版本隔离**：prompt / 模型 / 向量配置变化时整批失效，避免陈旧结果。

典型用法
--------
    result = cache_store.get_or_compute(
        resume_text=..., jd_ref="jd:1", position_category=..., experience_level=...,
        compute=lambda: run_analysis_and_rewrite(...), jd_id=1,
    )
    # result.source: memory | db | semantic | miss
"""

import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Tuple

from sqlalchemy import select, update

from .db import get_session
from .embedding import embed as embed_text
from .embedding import cosine, normalize
from .models import AnalysisCache, CacheStats

logger = logging.getLogger(__name__)

# 进程内 LRU 容量（仅作热路径加速，DB 才是真相源）
_MEMORY_MAX = 500

# 语义缓存开关与参数（均可通过环境变量调）
_SEMANTIC_ENABLED = os.getenv("SEMANTIC_CACHE_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
_SEMANTIC_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.95"))
# 每个分区最多扫描多少条做相似比较（防止全表扫；生产应换向量库）
_SEMANTIC_SCAN = int(os.getenv("SEMANTIC_CACHE_SCAN", "200"))


def cache_version() -> str:
    """缓存版本号：任一要素变化，旧缓存自动失效（无需手工清理）。

    要素包含 prompt 版本、LLM 模型、embedding 方式，以及 MOCK_LLM 开关。
    模型/提示词升级后若继续复用旧结果，会返回「按旧标准」生成的分析——
    这是缓存最常见的隐蔽 bug，故把版本直接编进缓存键。

    特别注意 MOCK_LLM：模拟文本与真实 LLM 输出是两回事，若不计入版本，
    从 mock 切到真 AI 时旧的模拟分析结果会被继续当作有效缓存返回。
    """
    base = os.getenv("CACHE_VERSION", "v1")
    model = os.getenv("LLM_MODEL", os.getenv("LLM_PROVIDER", "default"))
    emb = f"{os.getenv('EMBEDDING_PROVIDER', 'local')}{os.getenv('EMBEDDING_DIM', '256')}"
    mock = os.getenv("MOCK_LLM", "").strip().lower()
    mock_flag = "mock" if mock in ("1", "true", "yes", "on") else "real"
    return f"{base}|{model}|{emb}|{mock_flag}"


@dataclass
class CacheResult:
    """一次缓存查询的返回。"""

    cache_key: str
    resume_hash: str
    jd_id: Optional[int]
    score: int
    analysis: str
    rewrite: str
    cache_hit: bool = False
    source: str = "miss"  # memory | db | semantic | miss
    cache_similarity: float = 0.0  # 仅 semantic 命中时 >0


@dataclass
class CacheStatsView:
    """缓存命中统计视图。"""

    hits: int = 0  # 精确命中
    semantic_hits: int = 0  # 语义近似命中
    misses: int = 0
    memory_size: int = 0
    hit_rate: float = 0.0
    threshold: float = _SEMANTIC_THRESHOLD
    semantic_enabled: bool = _SEMANTIC_ENABLED

    @property
    def total(self) -> int:
        return self.hits + self.semantic_hits + self.misses


class CacheStore:
    """三级缓存实现。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._memory: "OrderedDict[str, CacheResult]" = OrderedDict()
        # 进程内累计（重启清零，DB 表为权威长期值）
        self._mem_hits = 0
        self._mem_misses = 0

    # ---------- 工具 ----------
    @staticmethod
    def _hash(text: str) -> str:
        """对**归一化后**的文本取哈希。

        归一化让「多个空格 / 大小写不同 / 标点差异」这类无语义差别的改动
        仍能命中同一份缓存（行业称 query normalization）。
        """
        return hashlib.md5(normalize(text).encode("utf-8")).hexdigest()

    @staticmethod
    def _cache_key(resume_hash: str, jd_ref: str, version: str) -> str:
        """jd_ref 可以是 'jd:<id>'（库内岗位）或 'text:<md5>'（用户直传 JD 文本）。

        版本编进键里：版本一变，键就变，旧条目自然不再命中。
        """
        return hashlib.md5(f"{version}|{resume_hash}|{jd_ref}".encode("utf-8")).hexdigest()

    # ---------- 内存层 ----------
    def _memory_get(self, key: str) -> Optional[CacheResult]:
        with self._lock:
            if key in self._memory:
                self._memory.move_to_end(key)
                # 返回副本，避免调用方串改内存中共享的缓存对象
                return replace(self._memory[key])
        return None

    def _memory_put(self, key: str, value: CacheResult) -> None:
        with self._lock:
            self._memory[key] = value
            self._memory.move_to_end(key)
            while len(self._memory) > _MEMORY_MAX:
                self._memory.popitem(last=False)

    # ---------- 语义层 ----------
    def _semantic_lookup(
        self, jd_ref: str, version: str, emb: List[float], s
    ) -> Tuple[Optional[AnalysisCache], float]:
        """在**同一 jd_ref 分区内**找最相似的缓存条目。

        返回 (行, 相似度)；无达标结果返回 (None, 0.0)。
        分区检索是防误命中的第一道闸——跨岗位比较毫无意义且危险。
        """
        if not _SEMANTIC_ENABLED:
            return None, 0.0
        rows = (
            s.scalars(
                select(AnalysisCache)
                .where(
                    AnalysisCache.jd_ref == jd_ref,
                    AnalysisCache.cache_version == version,
                    AnalysisCache.embedding.isnot(None),
                )
                .order_by(AnalysisCache.id.desc())
                .limit(_SEMANTIC_SCAN)
            ).all()
        )
        best: Optional[AnalysisCache] = None
        best_sim = 0.0
        for r in rows:
            try:
                vec = json.loads(r.embedding or "[]")
            except (ValueError, TypeError):
                continue
            if not vec:
                continue
            sim = cosine(emb, vec)
            if sim > best_sim:
                best, best_sim = r, sim
        if best is not None and best_sim >= _SEMANTIC_THRESHOLD:
            return best, best_sim
        return None, best_sim

    # ---------- 核心 API ----------
    def get_or_compute(
        self,
        resume_text: str,
        jd_ref: str,
        position_category: Optional[str],
        experience_level: Optional[str],
        compute: Callable[[], Dict[str, object]],
        jd_id: Optional[int] = None,
    ) -> CacheResult:
        """查缓存；命中直接返回，未命中调用 compute() 并落库。

        Args:
            resume_text: 简历原文（用于生成哈希、向量与缓存键）
            jd_ref: 岗位引用，'jd:<id>' 或 'text:<md5(jd_text)>'
            jd_id: 若为库内岗位则填其 id（落库便于检索，可空）
            position_category / experience_level: 仅用于落库便于检索
            compute: 缓存未命中时执行的耗时计算（通常是 LLM 分析+改写），
                     返回 {"score":int, "analysis":str, "rewrite":str}
        """
        version = cache_version()
        resume_hash = self._hash(resume_text)
        key = self._cache_key(resume_hash, jd_ref, version)

        # 1) L1 内存精确命中
        cached = self._memory_get(key)
        if cached is not None:
            with self._lock:
                self._mem_hits += 1
            self._bump_db_hits(key)
            logger.info("[cache] 内存命中 key=%s", key)
            cached.cache_hit = True
            cached.source = "memory"
            return cached

        # 2) L2 DB 精确命中
        with get_session() as s:
            row = s.scalar(select(AnalysisCache).where(AnalysisCache.cache_key == key))
            if row is not None:
                s.execute(
                    update(AnalysisCache)
                    .where(AnalysisCache.cache_key == key)
                    .values(hit_count=AnalysisCache.hit_count + 1)
                )
                s.execute(
                    update(CacheStats).where(CacheStats.id == 1).values(hits=CacheStats.hits + 1)
                )
                s.commit()
                with self._lock:
                    self._mem_hits += 1
                result = CacheResult(
                    cache_key=key,
                    resume_hash=resume_hash,
                    jd_id=jd_id,
                    score=row.score or 0,
                    analysis=row.analysis or "",
                    rewrite=row.rewrite or "",
                    cache_hit=True,
                    source="db",
                )
                self._memory_put(key, result)
                logger.info("[cache] DB 命中 key=%s (历史命中 %d 次)", key, row.hit_count)
                return result

            # 3) L3 语义近似命中（仅同分区 + 高阈值）
            try:
                emb = embed_text(resume_text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[cache] 向量化失败，跳过语义层：%s", exc)
                emb = []

            if emb:
                sim_row, sim = self._semantic_lookup(jd_ref, version, emb, s)
                if sim_row is not None:
                    s.execute(
                        update(AnalysisCache)
                        .where(AnalysisCache.id == sim_row.id)
                        .values(hit_count=AnalysisCache.hit_count + 1)
                    )
                    s.execute(
                        update(CacheStats)
                        .where(CacheStats.id == 1)
                        .values(semantic_hits=CacheStats.semantic_hits + 1)
                    )
                    s.commit()
                    with self._lock:
                        self._mem_hits += 1
                    result = CacheResult(
                        cache_key=key,
                        resume_hash=resume_hash,
                        jd_id=jd_id,
                        score=sim_row.score or 0,
                        analysis=sim_row.analysis or "",
                        rewrite=sim_row.rewrite or "",
                        cache_hit=True,
                        source="semantic",
                        cache_similarity=round(sim, 4),
                    )
                    # 回填到当前精确键，下次同文本可直接内存命中
                    self._memory_put(key, result)
                    logger.info(
                        "[cache] 语义命中 key=%s 相似度=%.4f (源条目 id=%d)",
                        key,
                        sim,
                        sim_row.id,
                    )
                    return result
                if sim > 0:
                    logger.info(
                        "[cache] 语义未达标 best=%.4f < 阈值 %.2f，转 LLM", sim, _SEMANTIC_THRESHOLD
                    )

        # 4) 未命中：计算 + 落库
        with self._lock:
            self._mem_misses += 1
        computed = compute()
        score = int(computed.get("score", 0))
        analysis = str(computed.get("analysis", ""))
        rewrite = str(computed.get("rewrite", ""))

        emb_json = None
        if emb:
            try:
                emb_json = json.dumps([round(v, 4) for v in emb])
            except (TypeError, ValueError):
                emb_json = None

        with get_session() as s:
            s.execute(
                update(CacheStats).where(CacheStats.id == 1).values(misses=CacheStats.misses + 1)
            )
            s.add(
                AnalysisCache(
                    cache_key=key,
                    resume_hash=resume_hash,
                    jd_ref=jd_ref,
                    cache_version=version,
                    embedding=emb_json,
                    jd_id=jd_id,
                    position_category=position_category,
                    experience_level=experience_level,
                    score=score,
                    analysis=analysis,
                    rewrite=rewrite,
                    hit_count=0,
                )
            )
            s.commit()

        result = CacheResult(
            cache_key=key,
            resume_hash=resume_hash,
            jd_id=jd_id,
            score=score,
            analysis=analysis,
            rewrite=rewrite,
            cache_hit=False,
            source="miss",
        )
        self._memory_put(key, result)
        logger.info("[cache] 未命中，已计算并落库 key=%s", key)
        return result

    def _bump_db_hits(self, key: str) -> None:
        """命中内存时仍累加 DB 的统计与行内 hit_count。"""
        try:
            with get_session() as s:
                s.execute(
                    update(AnalysisCache)
                    .where(AnalysisCache.cache_key == key)
                    .values(hit_count=AnalysisCache.hit_count + 1)
                )
                s.execute(
                    update(CacheStats).where(CacheStats.id == 1).values(hits=CacheStats.hits + 1)
                )
                s.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[cache] 累加 DB 命中数失败（不影响返回）：%s", exc)

    # ---------- 统计 ----------
    def stats(self) -> CacheStatsView:
        """返回进程内 + DB 累计的命中统计（含语义命中拆分）。"""
        with self._lock:
            mem_size = len(self._memory)
        hits = semantic_hits = misses = 0
        try:
            with get_session() as s:
                row = s.scalar(select(CacheStats).where(CacheStats.id == 1))
                if row:
                    hits = int(row.hits or 0)
                    misses = int(row.misses or 0)
                    semantic_hits = int(getattr(row, "semantic_hits", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[cache] 读取 DB 统计失败：%s", exc)

        total = hits + semantic_hits + misses
        rate = round((hits + semantic_hits) / total, 4) if total else 0.0
        return CacheStatsView(
            hits=hits,
            semantic_hits=semantic_hits,
            misses=misses,
            memory_size=mem_size,
            hit_rate=rate,
        )


# 模块级单例（与 graph agents 同生命周期）
cache_store = CacheStore()
