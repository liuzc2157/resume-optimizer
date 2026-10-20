"""LangGraph 节点函数定义。

每个节点函数遵循同一约定：
- 输入：当前 AgentState；
- 输出：一个 dict，只包含本节点要更新的字段（LangGraph 会将其合并回 State）；
- 所有 LLM 调用统一走 llm.call_llm。
"""

import logging
import re
from typing import Any, Dict

from .llm import call_llm

logger = logging.getLogger(__name__)

# analyze_node 的 system prompt：要求模型在第一行以「SCORE: 数字」格式给出分数，
# 便于用正则可靠地提取；这是结构化输出最简单的做法，避免引入额外的 JSON 解析失败面。
ANALYZE_SYSTEM_PROMPT = """你是一位资深的技术招聘官与简历顾问。
请对比候选人的简历与目标岗位 JD，输出一份匹配度分析报告。

输出格式要求（严格遵守）：
1. 第一行必须是「SCORE: <1-100 的整数>」，表示简历与 JD 的匹配度得分；
2. 之后按下面四块分点输出，每块用「## 」小标题，块内用「- 」开头分条，一条一句话，不要长段落：
   ## 匹配亮点
   ## 缺失或不足
   ## 风险点与招聘官可能的疑虑
   ## 具体建议
3. 全篇禁止使用「──」「——」「===」「---」「___」这类横线或分隔符分隔内容，靠小标题和空行区分层级。

分析要求（按优先级）：
1. **先看身份是否匹配**：若 JD 是「2026 届 / 应届 / 校园招聘」这类校招岗，而候选人已毕业工作若干年，
   必须把「身份超配」列为第一风险点，并明确指出招聘官会追问：为什么来投应届岗？薪资预期是否匹配？
   职业规划是否错位？这比技能匹配更值得提醒。
2. **不要臆断简历没写的信息**：判断学历、年限是否满足前，先看 JD 原文的具体表述。
   不要因为候选人毕业年份较早就直接得出「学历不满足」的结论——毕业早不等于学历低。
3. **缺失技能逐条对照 JD 原文**，不要凭空补充 JD 里没写的要求。
4. 结论要能指导行动：说清哪一条不满足、对方会怎么想、建议怎么补。"""

REWRITE_SYSTEM_PROMPT = """你是一位专业的简历改写专家。基于以下匹配度分析报告，对简历进行针对性优化。

文风要求（严格遵守）：
1. 只基于简历中真实存在的信息改写，可以调整表述、补充量化细节的建议，但严禁虚构新经历；
2. 严禁擅自拔高熟练度：原文「熟悉」不得改成「精通」，「了解」不得改成「掌握」，除非原文明确支持；
3. 严禁空话套话：不要出现「丰富的项目经验」「扎实的功底」「优秀的能力」这类没有信息量的形容词；
4. 每一条改写建议都要具体到「改哪里、怎么改、为什么」，给出可直接替换的原文和改后文本；
5. 语气平实克制，像资深招聘官给朋友的建议，不像营销文案。

输出格式要求（严格遵守）。输出会被前端按 Markdown 渲染，**换行必须用 Markdown 语法**，
否则多个要点会被挤成同一段：
1. 改写建议：用「1. 2. 3.」编号，每条建议下面用「- 」列表分三行：
   - 位置：<板块>｜原文：<摘句>
   - 改后：<新写法>
   - 理由：<一句话>
2. 「## 优化后的完整简历」必须按正式简历排版：
   - 每个板块单独一个「## 」二级标题：教育背景 / 专业技能 / 实习经历 / 工作经历 /
     项目经历 / 证书与荣誉 / 自我评价；
   - 板块内容一律用「- 」无序列表，**一条一行**，严禁把多个要点写进同一行；
   - 一段经历的写法：「- <学校或公司>｜<专业或职位>｜<起止时间>」，其下的细节各自
     再占一个「- 」列表项；
   - 技能项写成「- <分类>：<技能 1>、<技能 2>」，一个分类一行。
3. 项目符号只能用 Markdown 的「- 」，**禁止**用「·」「•」「・」当项目符号；
   也禁止靠单个换行分行（Markdown 会把单换行渲染成空格）。
4. **全篇禁止使用横线或分隔线**（「──」「——」「===」「---」「___」「***」等），
   层级一律靠标题和空行区分。
5. 不要滥用加粗，只在「技能名 + 熟练度」处少量使用。"""


# 形如「────────」「=====」「----」「***」「___」的纯分隔线
_SEPARATOR_LINE_RE = re.compile(r"^\s*[─—\-=_*·•~]{3,}\s*$")
# 模型爱用的「伪项目符号」：它们不是 Markdown 列表标记，
# 单个换行又会被渲染成空格，结果是所有要点挤成一段。
_FAKE_BULLETS = "·•・●○▪◆‣"


def _normalize_markdown(text: str) -> str:
    """把模型输出规整成能正确渲染的 Markdown。

    处理三件事（都是实测踩过的坑）：
    1. 删掉纯装饰分隔线（模型会把原简历里的「────」原样复述回来）；
    2. 把「·」这类伪项目符号换成 Markdown 的「- 」，否则列表不成列表；
    3. 标题前补空行，保证被解析成标题而不是上一段的延续。
    """
    lines: list[str] = []
    for line in (text or "").split("\n"):
        if _SEPARATOR_LINE_RE.match(line):
            continue
        stripped = line.lstrip()
        if stripped and stripped[0] in _FAKE_BULLETS:
            indent = line[: len(line) - len(stripped)]
            line = f"{indent}- " + stripped[1:].lstrip()
        lines.append(line)

    # 标题前补空行
    spaced: list[str] = []
    for line in lines:
        if line.startswith("#") and spaced and spaced[-1].strip():
            spaced.append("")
        spaced.append(line)

    # 压缩连续空行
    out: list[str] = []
    blank = False
    for line in spaced:
        if not line.strip():
            if blank:
                continue
            blank = True
        else:
            blank = False
        out.append(line)
    return "\n".join(out).strip()

# 三档改写强度：只切换 system prompt 的「强度要求」，缓存键按强度隔离，互不污染。
INTENSITY_PROMPTS = {
    "light": """【本次改写强度：轻推微调】
只做最小改动：保留简历原有结构、措辞和风格，仅修正明显问题——
1. 补齐缺失的关键技能词（在不虚构的前提下，把已具备的能力用 JD 用词表述出来）；
2. 修正含糊表述，不重写段落；
3. 输出「优化后的完整简历」时须与原文高度接近，只呈现改动过的地方。""",
    "enhance": """【本次改写强度：关键词增强】
重点提升与 JD 的匹配度——
1. 逐条对照 JD 关键词，将简历中已具备但表述含糊的技能显式写出（不虚构）；
2. 强化量化成果与行动动词，把「参与了XX」改写成「主导/负责了XX，带来XX结果」；
3. 保留原文结构与真实信息，可合并重复表述，不整段重写。""",
    "rewrite": """【本次改写强度：整篇重写】
全面重构简历，使其最大化贴合目标岗位——
1. 按 JD 优先级重排各板块与要点顺序，突出 JD 最看重的技能与成果；
2. 用强行动动词 + 量化结果重写每条经历（仍然只基于简历真实信息）；
3. 删减与目标岗位无关的冗余内容；
4. 输出一份结构完整、可直接投递的新简历。""",
}


def parse_node(state: "AgentState") -> Dict[str, Any]:
    """节点 1：解析/预处理。

    MVP 阶段只做基础清洗与日志记录（去除首尾空白）。
    未来可在此接入文件解析（PDF/DOCX → 文本）。
    """
    resume_len = len(state["resume_text"].strip())
    jd_len = len(state["jd_text"].strip())
    logger.info("[parse_node] 收到输入：简历 %d 字符，JD %d 字符", resume_len, jd_len)

    return {
        "resume_text": state["resume_text"].strip(),
        "jd_text": state["jd_text"].strip(),
        "messages": [
            f"[parse_node] 输入解析完成：简历 {resume_len} 字符，JD {jd_len} 字符"
        ],
    }


def analyze_node(state: "AgentState") -> Dict[str, Any]:
    """节点 2：调用 DeepSeek 生成匹配度分析，并提取 1-100 的分数。"""
    analysis, score = analyze_resume(state["resume_text"], state["jd_text"])
    return {
        "analysis_result": analysis,
        "score": score,
        "messages": [f"[analyze_node] 匹配度分析完成，得分：{score}"],
    }


def analyze_resume(resume_text: str, jd_text: str, usage_bucket=None) -> tuple[str, int]:
    """纯函数版匹配度分析：给定简历与 JD，返回 (分析报告, 得分)。

    供 LangGraph 节点与缓存服务层复用，避免逻辑重复。
    """
    user_prompt = (
        f"【候选人简历】\n{resume_text}\n\n"
        f"【目标岗位 JD】\n{jd_text}\n\n"
        "请按系统要求的格式输出匹配度分析报告，第一行给出 SCORE。"
    )
    analysis = call_llm(ANALYZE_SYSTEM_PROMPT, user_prompt, usage_bucket=usage_bucket)

    # 从 LLM 输出中提取分数：优先匹配「SCORE: 数字」，兜底取全文第一个 1-100 的数字
    score = _extract_score(analysis)
    logger.info("[analyze] 分析完成，提取得分=%d", score)
    return analysis, score


def _extract_score(text: str, default: int = 75) -> int:
    """从模型输出中提取 1-100 的整数分数，失败时返回默认值。

    先尝试「SCORE: 88」这类标记格式；若失败，退化为取文本中
    第一个位于 1-100 区间的独立数字，保证节点永不抛异常。
    """
    marker = re.search(r"SCORE[:：\s]+(\d{1,3})", text, flags=re.IGNORECASE)
    if marker:
        value = int(marker.group(1))
        if 1 <= value <= 100:
            return value

    for match in re.finditer(r"\b(\d{1,3})\b", text):
        value = int(match.group(1))
        if 1 <= value <= 100:
            return value

    logger.warning("[analyze_node] 未能从 LLM 输出中提取分数，使用默认值 %d", default)
    return default


def rewrite_node(state: "AgentState") -> Dict[str, Any]:
    """节点 3：基于分析结果生成改写建议与优化后的简历段落。"""
    rewrite = rewrite_resume(
        state["resume_text"], state["jd_text"], state["analysis_result"],
        intensity=state.get("intensity", "enhance"),
    )
    logger.info("[rewrite_node] 改写完成，输出 %d 字符", len(rewrite))

    return {
        "rewrite_result": rewrite,
        "messages": [f"[rewrite_node] 简历改写完成（{len(rewrite)} 字符）"],
    }


def rewrite_resume(resume_text: str, jd_text: str, analysis: str, intensity: str = "enhance", usage_bucket=None) -> str:
    """纯函数版改写：给定简历、JD 与分析报告，返回改写建议文本。

    intensity 三档：light=轻推微调 / enhance=关键词增强 / rewrite=整篇重写，
    只改变 system prompt 的强度要求，结果随强度隔离缓存。
    """
    extra = INTENSITY_PROMPTS.get(intensity)
    system_prompt = (
        REWRITE_SYSTEM_PROMPT + ("\n\n" + extra if extra else "")
    )
    user_prompt = (
        f"【候选人简历】\n{resume_text}\n\n"
        f"【目标岗位 JD】\n{jd_text}\n\n"
        f"【匹配度分析报告】\n{analysis}\n\n"
        "请基于以上分析输出改写建议和优化后的段落。"
    )
    raw = call_llm(system_prompt, user_prompt, usage_bucket=usage_bucket)
    # 兜底：模型常会把原简历里的「────」横线原样带回来，统一清掉，改为靠小标题分段
    return _normalize_markdown(raw)


def format_node(state: "AgentState") -> Dict[str, Any]:
    """节点 4：汇总与收尾。

    MVP 阶段只打印最终得分并记录日志。
    未来可在此做格式化导出（PDF/DOCX）、落库或推送通知。
    """
    logger.info(
        "[format_node] Agent 运行结束，最终得分：%d", state["score"]
    )
    return {
        "messages": [f"[format_node] 流程结束，最终得分：{state['score']}"],
    }
