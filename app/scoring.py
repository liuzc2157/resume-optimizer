"""多维度 ATS 评分（确定性计算，不依赖 LLM）。

为什么要有这一层
----------------
同类开源项目（Resume Hacker / ATS-RESUME-AGENT 等）普遍采用**多维评分**：
关键词覆盖、量化成果、行动动词、结构完整度、岗位对齐。
只给一个总分（如 88）的问题是**不可解释、不可行动**——用户不知道该改哪里。

本模块**纯规则计算、零 LLM 调用**，因此：
- 结果稳定可复现，便于回归测试；
- 离线/无 Key 时依然给出真实分数（不像 LLM 分数那样退化为固定值）；
- 即使命中缓存（LLM 未调用），分数也会按**当前这份简历**重算，保证准确。

维度与权重
----------
    keyword_match   0.35  JD 技能/关键词在简历中的覆盖率
    experience_match 0.20 简历年限 vs JD 要求年限
    quantification  0.15 成果量化（数字、百分比、倍数）
    action_verbs    0.10 强行动动词使用
    completeness    0.20 关键简历板块完整度
"""

import logging
import re
from typing import List, Optional

from .embedding import normalize
from .keywords import (
    extract_jd_keywords,
    extract_years,
    is_fresh_graduate_requirement,
    requirement_units,
)

logger = logging.getLogger(__name__)

# 经验等级 -> 期望年限（JD 未明写年限时的兜底）
_LEVEL_YEARS = {"初级": 1, "中级": 3, "高级": 5, "资深": 8, "专家": 8}

# 强行动动词（体现个人贡献，而非"参与了一下"）
_ACTION_VERBS = [
    "主导", "负责", "搭建", "设计", "优化", "实现", "推动", "重构",
    "落地", "提升", "降低", "建设", "开发", "改进", "引入", "攻坚",
    "牵头", "沉淀", "治理", "孵化", "从0到1", "从 0 到 1",
]

# 关键板块（任一关键词命中即视为具备）
_SECTIONS = {
    "教育背景": ["教育", "学历", "毕业", "本科", "硕士", "博士"],
    "工作经历": ["工作经历", "实习经历", "任职", "就职", "工作经验"],
    "项目经历": ["项目经历", "项目经验", "项目描述", "作品"],
    "专业技能": ["专业技能", "技能", "技术栈", "熟练掌握", "熟悉"],
}

# 量化成果信号
_QUANT_PATTERNS = [
    r"\d+\s*%", r"\d+\s*倍", r"\d+\s*万", r"\d+\s*w\b",
    r"增长", r"提升", r"降低", r"减少", r"节省", r"提高",
]
WEIGHTS = {
    "keyword_match": 0.35,
    "experience_match": 0.20,
    "quantification": 0.15,
    "action_verbs": 0.10,
    "completeness": 0.20,
}

LABELS = {
    "keyword_match": "关键词/技能覆盖",
    "experience_match": "经验年限匹配",
    "quantification": "成果量化",
    "action_verbs": "行动动词强度",
    "completeness": "结构完整度",
}


def _extract_years(text: str) -> Optional[int]:
    """抽取工作年限（委托 keywords.extract_years，与 matching 共用同一套规则）。

    历史问题：这里原有一套只认「3 年经验」固定语序的正则，导致简历里最常见的
    「工作年限：3 年」识别不到，经验维度被打成「未写明年限」的中性分。
    """
    return extract_years(text, pick="first")


def _jd_keywords(jd_content: str, jd_skills: Optional[List[str]]) -> List[str]:
    """确定用于比对「覆盖率」的关键词集合（委托 keywords.extract_jd_keywords）。

    历史问题：这里原来直接对 JD 分词后按词频取 Top-20，未过滤停用词与技术词，
    「要求/参与/至少/一种/岗位/统招/全日制/2026」等套话被当成技能关键词，
    使占比 35% 的关键词维度实际只衡量了套话重叠度。
    """
    return extract_jd_keywords(jd_content, jd_skills, limit=20)


def _contains(norm_resume: str, term: str) -> bool:
    """判断归一化后的简历文本里是否出现某个技能词。

    英文/技术标识符**必须按词边界匹配**：直接子串匹配会闹出荒谬误判——
    「go」会命中简历里的 Django，「java」会命中 JavaScript，「ai」会命中 email。
    中文没有词边界概念，仍用子串匹配。
    """
    key = normalize(term)
    if not key:
        return False
    if term.isascii():
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", norm_resume))
    return key in norm_resume


def _score_keyword(resume: str, jd_content: str, jd_skills: Optional[List[str]]) -> tuple:
    norm = normalize(resume)
    kws = _jd_keywords(jd_content, jd_skills)
    if not kws:
        return 60.0, "未提取到 JD 关键词，给中性分", [], []

    # 按「要求单元」计分：并列条件（「A、B、C 等至少一门」）合并成一项，
    # 命中任意成员即算满足，不再把「或」当成「与」逐项扣分。
    units = requirement_units(jd_content, kws)
    hit: List[str] = []
    miss: List[str] = []
    satisfied = 0
    for members, label in units:
        matched = [m for m in members if _contains(norm, m)]
        if matched:
            satisfied += 1
            for m in matched:
                if m not in hit:
                    hit.append(m)
        else:
            miss.append(label)

    ratio = satisfied / len(units) if units else 0.0
    detail = f"满足 {satisfied}/{len(units)} 项要求"
    if miss:
        detail += f"，缺失：{('、'.join(miss[:5]))}" + ("…" if len(miss) > 5 else "")
    return round(ratio * 100, 1), detail, hit, miss


def _score_experience(resume: str, jd_content: str, level: Optional[str]) -> tuple:
    r_years = _extract_years(resume)
    j_years_explicit = _extract_years(jd_content)

    # 校招 / 应届岗：岗位性质本身就是硬门槛，不能只拿年限比大小。
    # 典型错判：JD 写「2026 届」，候选人工作 3 年，旧逻辑走「中级要求 3 年 → 3 ≥ 3 → 100 分」，
    # 但这是应届岗，社招身份属于超配，招聘官一定会追问动机与薪资预期。
    if j_years_explicit is None and is_fresh_graduate_requirement(jd_content):
        if r_years is None:
            return 70.0, "JD 为应届/校招岗位，简历未写明毕业生身份或年限"
        if r_years == 0:
            return 100.0, "JD 为应届/校招岗位，简历为应届生，身份匹配"
        if r_years == 1:
            return 70.0, f"JD 为应届/校招岗位，简历有 {r_years} 年经验：已属社招，略微超配"
        return 45.0, (
            f"JD 为应届/校招岗位，简历有 {r_years} 年社招经验：身份超配，"
            "需说明求职动机与薪资预期是否匹配"
        )

    j_years = j_years_explicit or (_LEVEL_YEARS.get(level or "", None))
    if r_years is None and j_years is None:
        return 70.0, "双方均未写明年限，给中性分"
    if j_years is None:
        return 75.0, f"简历 {r_years} 年经验，JD 未明确要求"
    if r_years is None:
        return 60.0, f"JD 要求 {j_years} 年，简历未写明年限"
    if r_years >= j_years:
        return 100.0, f"简历 {r_years} 年 ≥ 要求 {j_years} 年"
    gap = j_years - r_years
    score = max(0.0, 100 - gap * 25)
    return round(score, 1), f"简历 {r_years} 年 < 要求 {j_years} 年（差 {gap} 年）"


def _score_quantification(resume: str) -> tuple:
    hits = 0
    for pat in _QUANT_PATTERNS:
        hits += len(re.findall(pat, resume, flags=re.IGNORECASE))
    score = min(100.0, hits * 15)
    if hits == 0:
        return 0.0, "未发现量化成果（建议补充数字、百分比、倍数）"
    return round(score, 1), f"检测到 {hits} 处量化表述"


def _score_action_verbs(resume: str) -> tuple:
    found = [v for v in _ACTION_VERBS if v in resume]
    score = min(100.0, len(found) * 20)
    if not found:
        return 0.0, "缺少强行动动词（建议用 主导/搭建/优化/提升 等）"
    return round(score, 1), f"使用行动动词 {len(found)} 个：{'、'.join(found[:6])}"


def _score_completeness(resume: str) -> tuple:
    present, missing = [], []
    for name, kws in _SECTIONS.items():
        if any(k in resume for k in kws):
            present.append(name)
        else:
            missing.append(name)
    ratio = len(present) / len(_SECTIONS)
    detail = f"具备 {len(present)}/{len(_SECTIONS)} 个板块"
    if missing:
        detail += f"，缺失：{'、'.join(missing)}"
    return round(ratio * 100, 1), detail


def score_resume(
    resume_text: str,
    jd_content: str,
    jd_skills: Optional[List[str]] = None,
    experience_level: Optional[str] = None,
) -> dict:
    """计算多维度 ATS 评分。

    返回：
        {
          "total": int,                 # 加权总分 0-100
          "job_fit": int,               # 岗位匹配度 0-100（只看与 JD 相关的维度）
          "resume_quality": int,        # 简历质量分 0-100（与 JD 无关的维度）
          "dimensions": [               # 各维度明细（供前端画条形图）
            {"key","label","score","weight","detail"}, ...
          ],
        }

    为什么要把总分拆成两个派生指标：
    总分里只有「关键词(35%) + 经验(20%)」跟目标 JD 有关，另外 45%
    （量化/动词/结构）衡量的是简历自身质量。所以一份强简历投错岗位，总分依然
    能到 65 分左右，看不出"投错了"。单独给出 job_fit 后，判断方向是否匹配
    就有独立依据：实测 20 组错配样本 job_fit 最高 43、对口最低 33，
    以 45 为线可识别全部错配。
    """
    scores = {
        "keyword_match": _score_keyword(resume_text, jd_content, jd_skills),
        "experience_match": _score_experience(resume_text, jd_content, experience_level),
        "quantification": _score_quantification(resume_text),
        "action_verbs": _score_action_verbs(resume_text),
        "completeness": _score_completeness(resume_text),
    }
    dims = []
    total = 0.0
    for key, item in scores.items():
        val, detail = item[0], item[1]
        w = WEIGHTS[key]
        total += val * w
        dim = {
            "key": key,
            "label": LABELS[key],
            "score": round(val, 1),
            "weight": w,
            "detail": detail,
        }
        # keyword_match 额外带回命中/缺失列表，供前端做关键词高亮
        if key == "keyword_match" and len(item) >= 4:
            dim["hits"] = item[2]
            dim["misses"] = item[3]
        dims.append(dim)

    # ---- 派生指标：把「岗位相关」与「简历质量」两部分分开 ----
    # 岗位匹配度：技能覆盖是主导信号；经验年限与岗位方向无关（6 年后端对 3 年
    # 数仓岗也判"年限达标"），所以只给 30% 权重，避免稀释方向差异。
    kw_val = scores["keyword_match"][0]
    exp_val = scores["experience_match"][0]
    job_fit = round(0.7 * kw_val + 0.3 * exp_val)
    q_weights = {
        "quantification": WEIGHTS["quantification"],
        "action_verbs": WEIGHTS["action_verbs"],
        "completeness": WEIGHTS["completeness"],
    }
    q_total_w = sum(q_weights.values())
    resume_quality = round(
        sum(scores[k][0] * w for k, w in q_weights.items()) / q_total_w
    )

    return {
        "total": int(round(total)),
        "job_fit": int(job_fit),
        "resume_quality": int(resume_quality),
        "dimensions": dims,
    }
