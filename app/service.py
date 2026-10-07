"""业务编排层：把「智能匹配 → 缓存 → LLM 分析改写」串成两个 Agent 共用的能力。

- 任务型（/optimize）与对话型（/chat）都复用这里的 optimize_resume / match_resume，
  保证两条路线行为一致。
"""

import hashlib
import logging
from typing import List, Optional

from . import matching, nodes, scoring
from .cache import cache_store
from .db import get_session
from .matching import JDMatch
from .models import JD, MatchLog

logger = logging.getLogger(__name__)


def _resolve_jd(
    resume_text: str,
    jd_text: Optional[str],
    jd_id: Optional[int],
    category: Optional[str],
    level: Optional[str],
    s,
    save_jd: bool = True,
) -> tuple:
    """解析本次分析要用的 JD：优先级 jd_text > jd_id > 智能匹配。

    用户自带 jd_text 时，默认会**保存进岗位库**（source=user），
    这样下次遇到同样岗位可直接复用、也能被智能匹配检索到。

    返回 (jd_content, jd_obj_or_None, match_or_None)
    """
    if jd_text and jd_text.strip():
        content = jd_text.strip()
        if save_jd:
            jd = matching.find_or_create_jd_by_content(
                content,
                position_category=category,
                experience_level=level,
                source="user",
            )
            return content, jd, None
        return content, None, None
    if jd_id:
        jd = matching.get_jd(jd_id, s)
        if jd:
            return jd.content, jd, None
    matches: List[JDMatch] = matching.match_jds(
        resume_text, category, level, top_n=1, session=s
    )
    if matches:
        return matches[0].jd.content, matches[0].jd, matches[0]
    raise ValueError(
        "未找到可用 JD：请传入 jd_text，或先在岗位库中创建匹配岗位（也可用 /match 预览候选）。"
    )


def optimize_resume(
    resume_text: str,
    jd_text: Optional[str] = None,
    jd_id: Optional[int] = None,
    position_category: Optional[str] = None,
    experience_level: Optional[str] = None,
    top_n_matches: int = 3,
    save_jd: bool = True,
    intensity: str = "enhance",
) -> dict:
    """任务型优化主流程：匹配岗位 → 查缓存 →（未命中）LLM 分析改写。

    save_jd=True（默认）时，用户自带的 jd_text 会被保存进岗位库，
    下次遇到同样岗位可直接复用，无需重复粘贴。

    intensity 三档（light/enhance/rewrite）只影响改写 prompt；
    为防三档结果互相污染，缓存引用 jd_ref 会带上强度后缀。
    """
    s = get_session()
    try:
        content, jd, match = _resolve_jd(
            resume_text, jd_text, jd_id, position_category, experience_level, s,
            save_jd=save_jd,
        )
        jd_ref = f"jd:{jd.id}" if jd else f"text:{hashlib.md5(content.encode()).hexdigest()}"
        # 强度不同的改写结果不能互相命中缓存（同一 JD 的三档输出完全不同）
        jd_ref = f"{jd_ref}|intensity:{intensity}"
        jd_id_for_cache = jd.id if jd else None

        def compute() -> dict:
            analysis, score = nodes.analyze_resume(resume_text, content)
            rewrite = nodes.rewrite_resume(resume_text, content, analysis, intensity=intensity)
            return {"score": score, "analysis": analysis, "rewrite": rewrite}

        result = cache_store.get_or_compute(
            resume_text=resume_text,
            jd_ref=jd_ref,
            position_category=position_category or (jd.position_category if jd else None),
            experience_level=experience_level or (jd.experience_level if jd else None),
            compute=compute,
            jd_id=jd_id_for_cache,
        )

        # 写匹配日志
        s.add(
            MatchLog(
                resume_hash=result.resume_hash,
                position_category=position_category or (jd.position_category if jd else None),
                experience_level=experience_level or (jd.experience_level if jd else None),
                matched_jd_id=jd.id if jd else None,
                matched_title=jd.title if jd else None,
                similarity=match.similarity if match else None,
            )
        )
        s.commit()

        # 候选岗位（供前端展示「按岗位经验匹配」的 Top-N）
        matches = matching.match_jds(
            resume_text, position_category, experience_level, top_n=top_n_matches, session=s
        )
    finally:
        s.close()

    matched_jd = None
    if jd:
        matched_jd = {
            "jd_id": jd.id,
            "title": jd.title,
            "position_category": jd.position_category,
            "experience_level": jd.experience_level,
            "similarity": match.similarity if match else None,
        }

    # 多维度 ATS 评分：确定性规则计算，不受缓存影响。
    # 即使命中缓存（未调 LLM），分数也按「当前这份简历」重算，保证准确可解释。
    breakdown = scoring.score_resume(
        resume_text=resume_text,
        jd_content=content,
        jd_skills=(jd.skills if jd else None),
        experience_level=experience_level or (jd.experience_level if jd else None),
    )

    return {
        "score": breakdown["total"],
        # result.score 是 LLM 自评（或缓存里存的上次自评），与 ATS 分口径不同。
        # 两者都返回，避免「报告里写 90、页面显示 62」这种无从解释的落差。
        "llm_score": result.score,
        "breakdown": breakdown,
        "analysis": result.analysis,
        "rewrite": result.rewrite,
        "cache_hit": result.cache_hit,
        "cache_source": result.source,
        "cache_similarity": result.cache_similarity,
        "matched_jd": matched_jd,
        "matches": [
            {
                "jd_id": m.jd.id,
                "title": m.jd.title,
                "position_category": m.jd.position_category,
                "experience_level": m.jd.experience_level,
                "similarity": m.similarity,
            }
            for m in matches
        ],
    }


def match_resume(
    resume_text: str,
    position_category: Optional[str] = None,
    experience_level: Optional[str] = None,
    top_n: int = 5,
    use_rag: bool = False,
) -> dict:
    """智能匹配：返回按相似度排序的候选岗位列表。

    use_rag=True 时改用 RAG 向量检索召回（语义匹配），返回的 matches 字段与
    关键词模式完全一致（MatchedJDSchema 可直接解析），并额外带回检索用的
    query_text 与 mode 标记，便于前端区分展示。
    """
    if use_rag:
        from . import rag

        result = rag.rag_retrieve(resume_text, top_k=top_n)
        return {
            "matches": result["items"],
            "mode": "rag",
            "rag_query": result["query_text"],
        }
    matches = matching.match_jds(resume_text, position_category, experience_level, top_n=top_n)
    return {
        "matches": [
            {
                "jd_id": m.jd.id,
                "title": m.jd.title,
                "position_category": m.jd.position_category,
                "experience_level": m.jd.experience_level,
                "similarity": m.similarity,
            }
            for m in matches
        ],
        "mode": "keyword",
        "rag_query": None,
    }


def classify_resume(resume_text: str) -> dict:
    """岗位自动分类：把简历归入岗位类别与经验等级，并返回置信度分布。

    纯确定性计算（关键词命中），不调 LLM、零成本，可直接在匹配前预判方向。
    """
    return matching.classify_resume(resume_text)


def compare_resume(
    resume_text: str,
    jd_ids: List[int],
    with_llm: bool = False,
) -> dict:
    """同一份简历 vs 多个岗位，横向对比「投哪个最合适」。

    默认只算确定性 ATS 分（scoring.score_resume），**不调 LLM**——
    比对通常一次看好几个岗位，若每个都跑 AI 既慢又费钱；
    用户选定岗位后再单独做完整 AI 分析即可。

    with_llm=True 时才对每个岗位跑完整优化（会走缓存）。
    """
    items: List[dict] = []
    for jid in jd_ids:
        jd = matching.get_jd(jid)
        if jd is None:
            continue
        breakdown = scoring.score_resume(
            resume_text=resume_text,
            jd_content=jd.content,
            jd_skills=jd.skills,
            experience_level=jd.experience_level,
        )
        item = {
            "jd_id": jd.id,
            "title": jd.title,
            "position_category": jd.position_category,
            "experience_level": jd.experience_level,
            "score": breakdown["total"],
            "dimensions": breakdown["dimensions"],
            "llm_score": None,
            "cache_hit": False,
        }
        if with_llm:
            full = optimize_resume(resume_text=resume_text, jd_id=jd.id)
            item["llm_score"] = full.get("llm_score")
            item["cache_hit"] = full.get("cache_hit", False)
        items.append(item)

    items.sort(key=lambda x: x["score"], reverse=True)
    return {
        "items": items,
        "best_jd_id": items[0]["jd_id"] if items else None,
    }
