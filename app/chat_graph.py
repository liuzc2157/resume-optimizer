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
from typing import Annotated, Any, Dict, List, Optional, Tuple, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from .cache import cache_store
from .db import get_session
from .llm import call_llm, call_llm_messages
from .nodes import ANALYZE_SYSTEM_PROMPT, REWRITE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPT = """你是「简历优化 Agent」，一个专业的简历顾问，与用户多轮对话完成简历优化。

工作规则：
1. 当用户发送简历内容时，调用 update_resume 工具保存，并简短确认；
2. 当用户发送岗位 JD 时，调用 update_jd 工具保存，并简短确认；
3. 如果用户在同一条消息里同时提供了简历和 JD，必须依次调用 update_resume 和 update_jd 两个工具分别保存；
4. 当用户要求分析匹配度/打分时，调用 analyze_match 工具，然后用简洁友好的语言转述结果；
5. 当用户要求改写/优化简历时，调用 rewrite_resume 工具，然后转述结果；
6. 简历或 JD 缺失时：若用户已给出岗位类别/经验等级，你可直接调用 analyze/rewrite，
   系统会从岗位库智能匹配最合适 JD；否则主动引导用户补充；
7. 用户日常咨询（如简历写作技巧）直接回答，不需要调用工具；
8. 全程使用中文，语气专业友善，回复避免冗长。"""

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


def agent_node(state: ChatState) -> Dict[str, Any]:
    """LLM 决策节点：根据完整历史决定「调用工具」还是「直接回复」。"""
    reply = call_llm_messages(state["messages"], tools=TOOLS)
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


def _resolve_content(state_updates: Dict[str, Any], state: Dict[str, Any], resume_text: str) -> Tuple[str, Optional[Any]]:
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
        matches = matching.match_jds(resume_text, category, level, top_n=1, session=s)
        if matches:
            return matches[0].jd.content, matches[0].jd
    finally:
        s.close()
    return "", None


def _run_analysis(resume_text: str, state_updates: Dict[str, Any], state: Dict[str, Any]) -> Tuple[str, bool]:
    """匹配度分析；命中缓存则不调用 LLM。返回 (分析报告, cache_hit)。"""
    from . import nodes

    content, jd = _resolve_content(state_updates, state, resume_text)
    if not content:
        return "简历或 JD 尚未提供，无法分析。请先让用户提供缺失的材料，或指定岗位类别与经验等级。", False

    cat = state_updates.get("position_category", state.get("position_category", "")) or (jd.position_category if jd else None)
    lvl = state_updates.get("experience_level", state.get("experience_level", "")) or (jd.experience_level if jd else None)
    jd_ref = f"jd:{jd.id}" if jd else f"text:{hashlib.md5(content.encode()).hexdigest()}"

    def compute() -> dict:
        analysis, score = nodes.analyze_resume(resume_text, content)
        return {"score": score, "analysis": analysis, "rewrite": ""}

    res = cache_store.get_or_compute(resume_text, jd_ref, cat, lvl, compute, jd_id=jd.id if jd else None)
    return res.analysis, res.cache_hit


def _run_rewrite(resume_text: str, state_updates: Dict[str, Any], state: Dict[str, Any]) -> Tuple[str, bool]:
    """改写；命中缓存则不调用 LLM。返回 (改写文本, cache_hit)。"""
    from . import nodes

    content, jd = _resolve_content(state_updates, state, resume_text)
    if not content:
        return "简历或 JD 尚未提供，无法改写。请先让用户提供缺失的材料，或指定岗位类别与经验等级。", False

    cat = state_updates.get("position_category", state.get("position_category", "")) or (jd.position_category if jd else None)
    lvl = state_updates.get("experience_level", state.get("experience_level", "")) or (jd.experience_level if jd else None)
    jd_ref = f"w:jd:{jd.id}" if jd else f"w:text:{hashlib.md5(content.encode()).hexdigest()}"

    def compute() -> dict:
        analysis, _ = nodes.analyze_resume(resume_text, content)
        rewrite = nodes.rewrite_resume(resume_text, content, analysis)
        return {"score": 0, "analysis": "", "rewrite": rewrite}

    res = cache_store.get_or_compute(resume_text, jd_ref, cat, lvl, compute, jd_id=jd.id if jd else None)
    return res.rewrite, res.cache_hit


def tool_node(state: ChatState) -> Dict[str, Any]:
    """工具执行节点：执行 LLM 请求的所有工具，并把结果以 ToolMessage 回填。"""
    last = state["messages"][-1]
    tool_messages: List[dict] = []
    state_updates: Dict[str, Any] = {}
    cache_hit_any = False

    resume_text = state_updates.get("resume_text", state.get("resume_text", ""))
    jd_text = state_updates.get("jd_text", state.get("jd_text", ""))

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
            result, hit = _run_analysis(resume_text, state_updates, state)
            cache_hit_any = cache_hit_any or hit
            if hit:
                result = f"（⚡命中缓存，未调用模型）\n\n{result}"
        elif name == "rewrite_resume":
            result, hit = _run_rewrite(resume_text, state_updates, state)
            cache_hit_any = cache_hit_any or hit
            if hit:
                result = f"（⚡命中缓存，未调用模型）\n\n{result}"
        else:
            result = f"未知工具：{name}"

        logger.info("[tool_node] 执行工具 %s（cache_hit=%s）", name, cache_hit_any)
        tool_messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

    return {"messages": tool_messages, "last_cache_hit": cache_hit_any, **state_updates}


def build_chat_graph():
    """构建并编译对话 Agent（带 Checkpointer，支持多轮会话）。"""
    graph = StateGraph(ChatState)
    graph.add_node("agent_node", agent_node)
    graph.add_node("tool_node", tool_node)

    graph.set_entry_point("agent_node")
    graph.add_conditional_edges("agent_node", route_after_agent, ["tool_node", END])
    graph.add_edge("tool_node", "agent_node")

    return graph.compile(checkpointer=MemorySaver())


chat_agent = build_chat_graph()
