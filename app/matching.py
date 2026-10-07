"""JD 岗位库 + 智能匹配。

匹配思路（无向量库也能跑，纯粹、可解释、零额外成本）
----------------------------------------------------
1. 用 jieba 分词（缺失则退化为「中日韩 bigram + 英文单词」）；
2. 简历 tokens 与 JD tokens 求重叠系数（Overlap Coefficient），
   并对 JD 的 skills 标签命中做加权加成 —— 技能命中是招聘硬门槛；
3. 若用户指定了 position_category / experience_level，先按这两个维度
   过滤候选池，再在池内排序，体现「按岗位跟经验不同进行匹配」。

后续若接入向量库（如 pgvector / Milvus），只需替换 _similarity 实现，
对外 match_jds 签名不变。
"""

import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

from sqlalchemy import select

from .db import get_session
from .models import JD

logger = logging.getLogger(__name__)

_CJK = re.compile(r"[一-鿿]")
_EN_TOKEN = re.compile(r"[a-zA-Z0-9_+#.]{2,}")

_jieba = None


def _tokenize(text: str) -> set:
    """中文分词，返回 token 集合。优先 jieba，否则 bigram 兜底。"""
    global _jieba
    text = (text or "").lower()
    if _jieba is None:
        try:
            import jieba  # type: ignore

            _jieba = jieba
        except Exception:  # noqa: BLE001
            _jieba = False

    if _jieba:
        return {t for t in _jieba.cut(text) if len(t.strip()) >= 2 or _EN_TOKEN.match(t)}

    tokens: set = set()
    # 英文/数字词
    tokens.update(_EN_TOKEN.findall(text))
    # 中日韩连续串 -> 二元语法
    for seg in _CJK.findall(text):
        pass
    # 用正则把中文串切出来再做 bigram
    for cjk_run in re.findall(r"[一-鿿]+", text):
        for i in range(len(cjk_run) - 1):
            tokens.add(cjk_run[i : i + 2])
    return tokens


def _overlap(a: set, b: set) -> float:
    """重叠系数 = |A∩B| / min(|A|,|B|)，对短文本更友好。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / min(len(a), len(b))


@dataclass
class JDMatch:
    """单个匹配结果。"""

    jd: JD
    similarity: float


def _score(resume_tokens: set, jd: JD) -> float:
    """综合得分：文本重叠 + 技能加权。"""
    jd_tokens = _tokenize(jd.content)
    base = _overlap(resume_tokens, jd_tokens)
    skill_bonus = 0.0
    skills = jd.skills or []
    if skills:
        skill_set = {str(s).lower() for s in skills}
        # 简历里出现该技能即视为强信号
        hit = sum(1 for s in skill_set if s in resume_tokens or s in (jd_tokens & resume_tokens))
        skill_bonus = min(0.35, 0.07 * hit)  # 最多 +0.35
    return min(1.0, base + skill_bonus)


def match_jds(
    resume_text: str,
    position_category: Optional[str] = None,
    experience_level: Optional[str] = None,
    top_n: int = 3,
    session=None,
) -> List[JDMatch]:
    """按岗位类别 + 经验等级过滤候选，再做相似度排序。

    Args:
        resume_text: 简历原文
        position_category: 目标岗位类别（可选，用于缩小候选池）
        experience_level: 目标经验等级（可选）
        top_n: 返回前 N 个
        session: 外部传入可复用；否则内部新建
    """
    resume_tokens = _tokenize(resume_text)
    own_session = session is None
    s = session or get_session()
    try:
        stmt = select(JD)
        if position_category:
            stmt = stmt.where(JD.position_category == position_category)
        if experience_level:
            stmt = stmt.where(JD.experience_level == experience_level)
        candidates: Sequence[JD] = s.execute(stmt).scalars().all()
        if not candidates:
            # 维度过滤后为空，回退到全库匹配（保证总有结果）
            candidates = s.execute(select(JD)).scalars().all()

        scored = [(jd, _score(resume_tokens, jd)) for jd in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [JDMatch(jd=jd, similarity=round(sim, 4)) for jd, sim in scored[:top_n]]
    finally:
        if own_session:
            s.close()


def get_jd(jd_id: int, session=None) -> Optional[JD]:
    own = session is None
    s = session or get_session()
    try:
        return s.get(JD, jd_id)
    finally:
        if own:
            s.close()


def list_jds(position_category: Optional[str] = None, experience_level: Optional[str] = None) -> List[JD]:
    with get_session() as s:
        stmt = select(JD).order_by(JD.id)
        if position_category:
            stmt = stmt.where(JD.position_category == position_category)
        if experience_level:
            stmt = stmt.where(JD.experience_level == experience_level)
        return list(s.execute(stmt).scalars().all())


def add_jd(
    position_category: str,
    experience_level: str,
    title: str,
    content: str,
    skills: Optional[list] = None,
    source: str = "seed",
) -> JD:
    with get_session() as s:
        jd = JD(
            position_category=position_category,
            experience_level=experience_level,
            title=title,
            content=content,
            skills=skills,
            source=source,
        )
        s.add(jd)
        s.commit()
        s.refresh(jd)
        return jd


# ---------------- 岗位类别 / 经验等级 自动推断 ----------------
# 用户传入自定义 JD 时，若没显式给类别与等级，用关键词命中数推断，
# 这样保存下来的岗位能被「按岗位类别 + 经验等级」正确检索到。
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    # ===== 技术岗位类别（仅保留技术方向） =====
    "后端": ["后端", "服务端", "python", "java", "golang", "go", "node", "api",
             "mysql", "redis", "微服务", "spring", "fastapi", "django"],
    "前端": ["前端", "react", "vue", "javascript", "typescript", "css", "html",
             "webpack", "vite", "小程序", "next.js"],
    "算法": ["算法", "数据结构", "leetcode", "推荐", "搜索", "排序", "动态规划",
             "图算法", "最优化"],
    "AI": ["人工智能", "ai", "大模型", "llm", "深度学习", "机器学习", "nlp",
           "自然语言", "pytorch", "tensorflow", "rag", "agent", "智能体",
           "微调", "lora", "transformer", "多模态", "扩散模型", "prompt",
           "gpt", "embedding", "预训练", "推理加速"],
    "数据": ["数据分析", "数据仓库", "数仓", "sql", "hive", "spark", "flink",
             "bi", "报表", "指标体系", "etl"],
    "测试": ["测试", "qa", "自动化", "pytest", "selenium", "用例", "质量保障", "压测"],
    "运维": ["运维", "devops", "sre", "k8s", "kubernetes", "docker", "监控",
             "ci/cd", "发布", "稳定性"],
    "移动端": ["android", "ios", "移动端", "flutter", "react native", "kotlin",
               "swift", "跨端", "鸿蒙"],
    "嵌入式": ["嵌入式", "单片机", "stm32", "arm", "驱动", "固件", "firmware",
               "rtos", "硬件", "iot", "物联网"],
    "安全": ["安全", "渗透", "漏洞", "等保", "风控", "审计", "攻防"],
}

# 等级：先看年限，再看关键词
LEVEL_BY_YEARS: list[tuple[int, str]] = [(8, "资深"), (5, "高级"), (3, "中级"), (0, "初级")]
LEVEL_KEYWORDS: dict[str, list[str]] = {
    "资深": ["资深", "专家", "架构师", "principal", "staff", "技术负责人"],
    "高级": ["高级", "senior", "高工"],
    "中级": ["中级", "middle"],
    "初级": ["初级", "应届", "实习", "助理", "junior", "入门"],
}


def _extract_years(text: str) -> Optional[int]:
    """从 JD 文本里抽取要求年限（取最大值，如「3-5 年」取 5）。"""
    low = (text or "").lower()
    nums: list[int] = []
    for m in re.finditer(r"(\d{1,2})\s*[-~到至]?\s*(\d{1,2})?\s*年", low):
        for g in (m.group(1), m.group(2)):
            if g:
                nums.append(int(g))
    return max(nums) if nums else None


def _hits(low_text: str, keywords: Sequence[str]) -> list[str]:
    """返回命中的关键词列表。

    英文关键词必须**按词边界**匹配，否则会出现荒谬的误判：
    例如 "Linux" 里含 "ux" 会被当成「设计」，"django" 里含 "go" 会被当成 Go 语言。
    中文关键词没有词边界概念，仍用子串匹配。
    """
    matched: list[str] = []
    for kw in keywords:
        if kw.isascii():
            if re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", low_text):
                matched.append(kw)
        elif kw in low_text:
            matched.append(kw)
    return matched


def infer_category(text: str) -> str:
    """按关键词命中数推断岗位类别，无命中返回「通用」。"""
    low = (text or "").lower()
    best, best_hit = "通用", 0
    for cat, kws in CATEGORY_KEYWORDS.items():
        hit = len(_hits(low, kws))
        if hit > best_hit:
            best, best_hit = cat, hit
    return best


def infer_level(text: str) -> str:
    """先按年限推断经验等级，再回退到关键词。"""
    low = (text or "").lower()
    years = _extract_years(low)
    if years is not None:
        for threshold, level in LEVEL_BY_YEARS:
            if years >= threshold:
                return level
    for level, kws in LEVEL_KEYWORDS.items():
        if _hits(low, kws):
            return level
    return "中级"


def classify_resume(text: str) -> dict:
    """把简历/任意文本按关键词归类到岗位类别与经验等级，并返回置信度分布。

    与 infer_category 的区别：这里不只返回「最佳类别」，而是返回**所有命中类别的
    关键词证据分布**，便于前端展示「这份简历更偏后端还是算法」的可解释置信度。

    返回结构：
        {
            "category": str,            # 最佳类别（无命中为「通用」）
            "level": str,               # 推断的经验等级
            "confidence": float,        # 最佳类别占全部关键词证据的比例 0-1
            "category_scores": [        # 各命中类别的关键词数（降序）
                {"category": ..., "hits": int, "confidence": float}, ...
            ],
            "level_reason": {"years": Optional[int], "keywords": [str, ...]},
        }
    """
    low = (text or "").lower()
    scored: list[tuple[str, int]] = []
    total_hits = 0
    for cat, kws in CATEGORY_KEYWORDS.items():
        hit = len(_hits(low, kws))
        if hit > 0:
            scored.append((cat, hit))
            total_hits += hit
    scored.sort(key=lambda x: x[1], reverse=True)

    if scored:
        best_cat, best_hits = scored[0]
        # 置信度 = 最佳类别命中数 / 全部命中数（即该类占证据的比例）
        confidence = round(best_hits / total_hits, 4) if total_hits else 0.0
        category_scores = [
            {
                "category": c,
                "hits": h,
                "confidence": round(h / total_hits, 4) if total_hits else 0.0,
            }
            for c, h in scored
        ]
    else:
        best_cat, confidence, category_scores = "通用", 0.0, []

    # 等级推断（返回依据，便于前端解释）
    years = _extract_years(low)
    lvl_keywords: list[str] = []
    for _lvl, kws in LEVEL_KEYWORDS.items():
        lvl_keywords.extend(_hits(low, kws))
    level = infer_level(low)

    return {
        "category": best_cat,
        "level": level,
        "confidence": confidence,
        "category_scores": category_scores,
        "level_reason": {"years": years, "keywords": lvl_keywords},
    }


def find_or_create_jd_by_content(
    content: str,
    position_category: Optional[str] = None,
    experience_level: Optional[str] = None,
    title: Optional[str] = None,
    skills: Optional[list] = None,
    source: str = "user",
) -> JD:
    """用户传入自定义 JD 文本时的「查重 + 入库」。

    - 若库中已有**完全相同内容**的 JD，直接复用（避免重复堆积）；
    - 否则新建一条，类别/等级未指定时自动推断，source 标记为 user。

    返回库中的 JD 对象。
    """
    content_clean = (content or "").strip()
    category = position_category or infer_category(content_clean)
    level = experience_level or infer_level(content_clean)
    if not title:
        # 取首行/前 24 字作为标题，保证列表里能看清是哪个岗位
        first_line = content_clean.splitlines()[0] if content_clean else ""
        title = (first_line[:24].strip() or content_clean[:24].strip() or "自定义岗位")

    with get_session() as s:
        existing = s.scalar(select(JD).where(JD.content == content_clean))
        if existing is not None:
            return existing
        jd = JD(
            position_category=category,
            experience_level=level,
            title=title,
            content=content_clean,
            skills=skills,
            source=source,
        )
        s.add(jd)
        s.commit()
        s.refresh(jd)
        logger.info(
            "[matching] 已保存用户自定义 JD：id=%s 标题=%s 类别=%s 等级=%s",
            jd.id,
            jd.title,
            category,
            level,
        )
        return jd


def update_jd(jd_id: int, **fields) -> Optional[JD]:
    """修改已保存的 JD（只更新传入的字段）。"""
    allowed = {"position_category", "experience_level", "title", "content", "skills"}
    with get_session() as s:
        jd = s.get(JD, jd_id)
        if jd is None:
            return None
        for k, v in fields.items():
            if k in allowed and v is not None:
                setattr(jd, k, v)
        s.commit()
        s.refresh(jd)
        return jd


def delete_jd(jd_id: int) -> bool:
    with get_session() as s:
        jd = s.get(JD, jd_id)
        if jd is None:
            return False
        s.delete(jd)
        s.commit()
        return True


def count_jds() -> int:
    with get_session() as s:
        return len(s.execute(select(JD)).scalars().all())
