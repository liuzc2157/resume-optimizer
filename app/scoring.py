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
_YEAR_PATTERNS = [
    r"(\d+)\s*年(?:以上)?(?:的)?(?:工作|开发|从业|相关)?经验",
    r"(\d+)\s*\+?\s*years?[^。；\n]{0,10}experience",
    r"经验[：: ]*(\d+)\s*年",
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
    """从文本里抽取「X 年经验」的年限，抽不到返回 None。"""
    for pat in _YEAR_PATTERNS:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


def _jd_keywords(jd_content: str, jd_skills: Optional[List[str]]) -> List[str]:
    """确定用于比对「覆盖率」的关键词集合。

    优先用岗位库里结构化的 skills 字段；没有则从 JD 正文抽分词候选。
    """
    if jd_skills:
        return [str(s) for s in jd_skills if str(s).strip()]

    text = normalize(jd_content)
    try:  # pragma: no cover - jieba 可选
        import jieba  # type: ignore

        toks = [t for t in jieba.cut(text) if len(t) >= 2]
    except Exception:  # noqa: BLE001
        toks = re.findall(r"[a-zA-Z]{2,}|[\u4e00-\u9fff]{2,}", text)
    # 按出现频次取前 20 个作为关键词
    freq: dict[str, int] = {}
    for t in toks:
        freq[t] = freq.get(t, 0) + 1
    top = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:20]
    return [k for k, _ in top]


def _score_keyword(resume: str, jd_content: str, jd_skills: Optional[List[str]]) -> tuple:
    norm = normalize(resume)
    kws = _jd_keywords(jd_content, jd_skills)
    if not kws:
        return 60.0, "未提取到 JD 关键词，给中性分", [], []
    hit = []
    miss = []
    for k in kws:
        if normalize(k) and normalize(k) in norm:
            hit.append(k)
        else:
            miss.append(k)
    ratio = len(hit) / len(kws)
    detail = f"命中 {len(hit)}/{len(kws)}"
    if miss:
        detail += f"，缺失：{('、'.join(miss[:6]))}" + ("…" if len(miss) > 6 else "")
    return round(ratio * 100, 1), detail, hit, miss


def _score_experience(resume: str, jd_content: str, level: Optional[str]) -> tuple:
    r_years = _extract_years(resume)
    j_years = _extract_years(jd_content) or (_LEVEL_YEARS.get(level or "", None))
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
          "dimensions": [               # 各维度明细（供前端画条形图）
            {"key","label","score","weight","detail"}, ...
          ],
        }
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
    return {"total": int(round(total)), "dimensions": dims}
