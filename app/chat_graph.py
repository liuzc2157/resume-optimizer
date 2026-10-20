"""对话型简历优化 Agent —— LangGraph 循环图实现（v0.2 支持智能匹配 + 缓存）。

相比原版新增：
- ChatState 携带 position_category / experience_level / jd_id，让对话也能从岗位库智能匹配；
- analyze_match / rewrite_resume 在简历/JD 缺失时，按维度从岗位库匹配最合适的 JD；
- 两步都走 cache_store，命中缓存时 cache_hit=True、不再调用 LLM，并记录 last_cache_hit。

循环结构不变：
    START → agent_node ──（有 tool_calls）──→ tool_node → agent_node
                 └─────（无 tool_calls）──→ END
"""

import hashlib
import json
import logging
import operator
import re
from typing import Annotated, Any, Dict, List, Optional, Tuple, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from .checkpointer import get_db_checkpointer

from .cache import cache_store
from .db import get_session
from .llm import call_llm, call_llm_messages
from .nodes import ANALYZE_SYSTEM_PROMPT, REWRITE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPT = """你是「简历优化 Agent」，一个专业的简历顾问，与用户多轮对话完成简历优化。

工作规则：
1. 当用户发送简历内容时，调用 update_resume 工具保存；
2. 当用户发送岗位 JD 时，调用 update_jd 工具保存；
3. 如果用户在同一条消息里同时提供了简历和 JD，必须依次调用 update_resume 和 update_jd 两个工具分别保存；
4. 当用户要求分析匹配度/打分时，调用 analyze_match 工具；
5. 当用户要求改写/优化简历时，调用 rewrite_resume 工具；
6. 简历或 JD 缺失时：若用户已给出岗位类别/经验等级，你可直接调用 analyze/rewrite，
   系统会从岗位库智能匹配最合适 JD；否则主动引导用户补充；
7. 用户日常咨询（如简历写作技巧）直接回答，不需要调用工具；
8. 全程使用中文，语气专业友善，回复避免冗长。

回复措辞要求（严格遵守，违反即为错误回复）：
1. **只说这一轮真正做完的事**。本轮只保存了简历，就只能说「简历已记录／已收到」，
   并明确讲清下一步还需要什么，例如：
   「简历已收到（2026 届应届 · Python 后端方向）。请把目标岗位 JD 发给我，我再做匹配度分析和改写。」
2. **禁止承诺尚未发生的动作**。没有真正调用 analyze_match 或 rewrite_resume 时，
   绝对不许说「我会仔细分析」「请稍等片刻」「马上为你优化」「正在分析」这类话——
   这些动作只在你实际调用工具的那一轮里发生；
3. 本轮只保存了 JD，就说「JD 已记录」，并询问用户是要分析匹配度还是直接改写；
4. **忠实转述工具返回的结果**：不得新增、删减或改写其中的事实（岗位方向、技能、年限、
   学历、分数）。工具没提到的信息一律不要补，更不要凭空描述候选人具备某种经验；
5. 若工具返回「简历或 JD 尚未提供」，就照实告诉用户缺什么，不要自己编一份分析；
6. 不要复述用户粘贴的简历或 JD 原文。"""

# OpenAI function calling 格式的工具定义
TOOLS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "update_resume",
            "description": "保存用户提供的简历内容。当用户发送完整简历文本时必须调用。",
            "parameters": {
                "type": "object",
                "properties": {"resume_text": {"type": "string", "description": "简历全文"}},
                "required": ["resume_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_jd",
            "description": "保存用户提供的岗位 JD。当用户发送职位描述时必须调用。",
            "parameters": {
                "type": "object",
                "properties": {"jd_text": {"type": "string", "description": "JD 全文"}},
                "required": ["jd_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_match",
            "description": "对已保存的简历（必要时按岗位类别/经验等级从岗位库匹配 JD）做匹配度分析并给出 1-100 分数。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rewrite_resume",
            "description": "基于简历与 JD（必要时智能匹配）生成改写建议和优化后的简历段落。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class ChatState(TypedDict):
    """对话 Agent 的共享状态。"""

    messages: Annotated[List[dict], operator.add]
    resume_text: str
    jd_text: str
    position_category: str
    experience_level: str
    jd_id: int
    last_cache_hit: bool


# ---- 确定性入库：不依赖模型"记得"调工具 ----
# 线上缺陷：模型经常只回一句「简历已记录 / JD 已记录」而**不实际调用**工具，
# 于是 state.resume_text / jd_text 一直是空的。后续 analyze_match 又静默回落到
# 岗位库的一份替补 JD，最终用「空简历 × 别人的 JD」拼出一份凭空捏造的分析
# （线上出现过 Python 后端应届生被判为「有 3 年经验、移动端 Dart/React 跨端」）。
# 因此这里按文本特征做一次确定性受理，模型若额外调用工具也只是覆盖同一份内容。
_JD_MARKERS = (
    "岗位职责", "任职要求", "任职资格", "工作职责", "职位描述", "岗位要求",
    "工作地点", "薪资范围", "学历要求", "岗位名称",
)
_RESUME_MARKERS = (
    "教育背景", "工作经历", "项目经历", "实习经历", "专业技能", "自我评价",
    "求职意向", "主修课程", "毕业", "学历",
)


def _classify_pasted(text: str) -> str:
    """判断用户粘贴的是一份简历、一份 JD，还是普通提问。"""
    t = text or ""
    if len(t) < 30:
        return "other"
    jd_hits = sum(1 for m in _JD_MARKERS if m in t)
    resume_hits = sum(1 for m in _RESUME_MARKERS if m in t)
    # 简历里也可能写「求职意向」「学历」，JD 里也可能写「项目经验」，
    # 所以用命中数量对比而不是"任一命中"。
    if jd_hits >= 2 and jd_hits > resume_hits:
        return "jd"
    if resume_hits >= 2 and resume_hits > jd_hits:
        return "resume"
    return "other"


def ingest_node(state: ChatState) -> Dict[str, Any]:
    """入库节点：把本轮用户粘贴的简历/JD 确定性地写进 state。

    放在 agent_node 之前，保证「先有材料、再有分析」的顺序，不依赖模型的工具调用。
    """
    last_user = next(
        (m.get("content") or "" for m in reversed(state["messages"]) if m.get("role") == "user"),
        "",
    )
    kind = _classify_pasted(last_user)
    if kind == "jd":
        logger.info("[ingest] 识别为岗位 JD（%d 字），已入库", len(last_user))
        return {"jd_text": last_user.strip()}
    if kind == "resume":
        logger.info("[ingest] 识别为简历（%d 字），已入库", len(last_user))
        return {"resume_text": last_user.strip()}
    return {}


def agent_node(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """LLM 决策节点：根据完整历史决定「调用工具」还是「直接回复」。"""
    usage_bucket = (config or {}).get("configurable", {}).get("usage_bucket")
    reply = call_llm_messages(
        _sanitize_messages(state["messages"]), tools=TOOLS, usage_bucket=usage_bucket
    )
    msg: dict = {"role": "assistant", "content": reply.content or ""}
    if reply.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in reply.tool_calls
        ]
    return {"messages": [msg]}


def route_after_agent(state: ChatState) -> str:
    """条件路由：有 tool_calls → 执行工具；否则对话结束。"""
    last = state["messages"][-1]
    return "tool_node" if last.get("tool_calls") else END


# 分析报告第一行的「SCORE: 70」，展示前要剥掉（它由独立字段承载）
_SCORE_LINE_RE = re.compile(r"^\s*SCORE\s*[:：]\s*(\d{1,3})\s*$", re.IGNORECASE | re.MULTILINE)


def _format_analysis_reply(analysis: str, cache_hit: bool) -> str:
    """把分析原文整理成可直接展示的回复。

    为什么不让模型转述：工具已经产出了完整分析，再让模型"用友好的语言转述一遍"
    纯属多此一举，而且小模型会把结论改写走样（实测出现过把 Python 后端简历
    说成「拥有移动端 / Dart / React 跨端经验」）。这里直接回原文，杜绝失真。
    """
    text = (analysis or "").strip()
    m = _SCORE_LINE_RE.search(text)
    score = m.group(1) if m else None
    if m:
        text = (text[: m.start()] + text[m.end():]).strip()
    head = "## 匹配度分析"
    if score:
        head += f"（自评 {score} / 100）"
    if cache_hit:
        head += " · 命中缓存"
    return f"{head}\n\n{text}" if text else head


def route_after_tool(state: ChatState) -> str:
    """工具已直接产出回复（分析/改写）时结束本轮，不再让模型二次转述。"""
    last = state["messages"][-1]
    return END if last.get("role") == "assistant" else "agent_node"


def _sanitize_messages(messages: List[dict]) -> List[dict]:
    """清洗发给模型的消息列表，避免因空消息被供应商判为「messages 参数非法」。

    实测：assistant 消息 content 为空且没有 tool_calls 时，部分 OpenAI 兼容服务
    （如智谱）会直接返回 400，导致整轮对话失败。
    """
    cleaned: List[dict] = []
    for m in messages or []:
        msg = dict(m)
        if msg.get("content") is None:
            msg["content"] = ""
        if msg.get("role") == "assistant" and not msg.get("content") and not msg.get("tool_calls"):
            continue
        cleaned.append(msg)
    return cleaned


def _resolve_content(
    state_updates: Dict[str, Any], state: Dict[str, Any], resume_text: str, user_id: Optional[int] = None
) -> Tuple[str, Optional[Any]]:
    """解析本轮要分析的 JD 文本：jd_text > jd_id > 智能匹配。返回 (content, jd_or_None)。"""
    from . import matching

    jd_text = state_updates.get("jd_text", state.get("jd_text", ""))
    if jd_text and jd_text.strip():
        return jd_text.strip(), None

    jd_id = state_updates.get("jd_id", state.get("jd_id", 0)) or 0
    category = state_updates.get("position_category", state.get("position_category", "")) or None
    level = state_updates.get("experience_level", state.get("experience_level", "")) or None

    s = get_session()
    try:
        if jd_id:
            jd = matching.get_jd(jd_id, s)
            if jd:
                return jd.content, jd
        # 只有用户**显式**给了岗位类别/经验等级，才允许从岗位库挑一份 JD。
        # 否则一律返回空、由调用方提示补材料——绝不能拿一份无关的种子 JD 顶替，
        # 那会让模型对着别人的岗位要求编分析（线上就是这么编出「3 年经验」的）。
        if not (category or level):
            return "", None
        matches = matching.match_jds(resume_text, category, level, top_n=1, session=s)
        if matches:
            return matches[0].jd.content, matches[0].jd
    finally:
        s.close()
    return "", None


def _missing_materials_reply(resume_text: str) -> str:
    """材料缺失时给出的用户可读提示（明确还缺什么，而不是含糊报错）。"""
    need: List[str] = []
    if not (resume_text or "").strip():
        need.append("候选人简历")
    need.append("目标岗位 JD（或者告诉我岗位类别 + 经验等级，我从岗位库匹配）")
    return "还缺少以下材料，暂时无法分析：\n" + "\n".join(f"- {n}" for n in need)


def _run_analysis(
    resume_text: str, state_updates: Dict[str, Any], state: Dict[str, Any],
    user_id: Optional[int] = None, usage_bucket: Optional[dict] = None,
) -> Tuple[str, bool]:
    """匹配度分析；命中缓存则不调用 LLM。返回 (分析报告, cache_hit)。"""
    from . import nodes

    content, jd = _resolve_content(state_updates, state, resume_text, user_id=user_id)
    if not content:
        return _missing_materials_reply(resume_text), False

    cat = state_updates.get("position_category", state.get("position_category", "")) or (jd.position_category if jd else None)
    lvl = state_updates.get("experience_level", state.get("experience_level", "")) or (jd.experience_level if jd else None)
    jd_ref = f"jd:{jd.id}" if jd else f"text:{hashlib.md5(content.encode()).hexdigest()}"

    def compute() -> dict:
        analysis, score = nodes.analyze_resume(resume_text, content, usage_bucket=usage_bucket)
        return {"score": score, "analysis": analysis, "rewrite": ""}

    res = cache_store.get_or_compute(
        resume_text, jd_ref, cat, lvl, compute, jd_id=jd.id if jd else None, user_id=user_id
    )
    return res.analysis, res.cache_hit


def _run_rewrite(
    resume_text: str, state_updates: Dict[str, Any], state: Dict[str, Any],
    user_id: Optional[int] = None, usage_bucket: Optional[dict] = None,
) -> Tuple[str, bool]:
    """改写；命中缓存则不调用 LLM。返回 (改写文本, cache_hit)。"""
    from . import nodes

    content, jd = _resolve_content(state_updates, state, resume_text, user_id=user_id)
    if not content:
        return _missing_materials_reply(resume_text), False

    cat = state_updates.get("position_category", state.get("position_category", "")) or (jd.position_category if jd else None)
    lvl = state_updates.get("experience_level", state.get("experience_level", "")) or (jd.experience_level if jd else None)
    jd_ref = f"w:jd:{jd.id}" if jd else f"w:text:{hashlib.md5(content.encode()).hexdigest()}"

    def compute() -> dict:
        analysis, _ = nodes.analyze_resume(resume_text, content, usage_bucket=usage_bucket)
        rewrite = nodes.rewrite_resume(resume_text, content, analysis, usage_bucket=usage_bucket)
        return {"score": 0, "analysis": "", "rewrite": rewrite}

    res = cache_store.get_or_compute(
        resume_text, jd_ref, cat, lvl, compute, jd_id=jd.id if jd else None, user_id=user_id
    )
    return res.rewrite, res.cache_hit


def tool_node(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """工具执行节点：执行 LLM 请求的所有工具，并把结果以 ToolMessage 回填。

    config 里的 user_id 用于：会话归属隔离 + 缓存/匹配按用户作用域。
    """
    cfg = (config or {}).get("configurable", {}) or {}
    user_id = cfg.get("user_id")
    usage_bucket = cfg.get("usage_bucket")
    last = state["messages"][-1]
    tool_messages: List[dict] = []
    state_updates: Dict[str, Any] = {}
    cache_hit_any = False

    resume_text = state_updates.get("resume_text", state.get("resume_text", ""))
    jd_text = state_updates.get("jd_text", state.get("jd_text", ""))
    reply_text: Optional[str] = None

    for tc in last.get("tool_calls", []):
        name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"]["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {}

        result: str
        if name == "update_resume":
            resume_text = args.get("resume_text", "").strip()
            state_updates["resume_text"] = resume_text
            result = "简历已保存。"
        elif name == "update_jd":
            jd_text = args.get("jd_text", "").strip()
            state_updates["jd_text"] = jd_text
            result = "JD 已保存。"
        elif name == "analyze_match":
            result, hit = _run_analysis(
                resume_text, state_updates, state, user_id=user_id, usage_bucket=usage_bucket
            )
            cache_hit_any = cache_hit_any or hit
            # 分析原文直接作为本轮回复，不让模型二次转述（避免结论被改写走样）
            reply_text = _format_analysis_reply(result, hit)
            if hit:
                result = f"（⚡命中缓存，未调用模型）\n\n{result}"
        elif name == "rewrite_resume":
            result, hit = _run_rewrite(
                resume_text, state_updates, state, user_id=user_id, usage_bucket=usage_bucket
            )
            cache_hit_any = cache_hit_any or hit
            reply_text = result
            if hit:
                result = f"（⚡命中缓存，未调用模型）\n\n{result}"
        else:
            result = f"未知工具：{name}"

        logger.info("[tool_node] 执行工具 %s（cache_hit=%s）", name, cache_hit_any)
        tool_messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

    out_messages = list(tool_messages)
    if reply_text:
        # 追加为最终 assistant 消息，route_after_tool 看到它就直接结束本轮
        out_messages.append({"role": "assistant", "content": reply_text})
    return {"messages": out_messages, "last_cache_hit": cache_hit_any, **state_updates}


def build_chat_graph():
    """构建并编译对话 Agent（带 Checkpointer，支持多轮会话）。"""
    graph = StateGraph(ChatState)
    graph.add_node("ingest_node", ingest_node)
    graph.add_node("agent_node", agent_node)
    graph.add_node("tool_node", tool_node)

    # 先做确定性入库（不依赖模型调工具），再交给模型决策
    graph.set_entry_point("ingest_node")
    graph.add_edge("ingest_node", "agent_node")
    graph.add_conditional_edges("agent_node", route_after_agent, ["tool_node", END])
    # 分析/改写已产出最终回复 → 直接结束；只做了保存 → 回到 agent 生成确认语
    graph.add_conditional_edges("tool_node", route_after_tool, ["agent_node", END])

    return graph.compile(checkpointer=get_db_checkpointer())


chat_agent = build_chat_graph()
