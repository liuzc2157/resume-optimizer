"""LangGraph StateGraph 定义 —— 项目的核心编排层。

为什么要用 LangGraph 而不是普通函数链：
1. 显式的节点/边定义让流程一目了然，新增节点只需 add_node + add_edge；
2. 结构化 State（AgentState）在各节点间流转，字段更新由 LangGraph 统一合并，
   节点之间不直接耦合；
3. 原生支持 Conditional Edge（条件路由）与 Checkpointer（断点续跑），
   后续加入「评分过低重试」「人审 Human-in-the-loop」等分支无需重构。

当前 MVP 为单向 DAG：parse → analyze → rewrite → format。
"""

import operator
from typing import Annotated, Dict, List, TypedDict

from langgraph.graph import END, StateGraph

from .nodes import analyze_node, format_node, parse_node, rewrite_node


class AgentState(TypedDict):
    """Agent 的全局共享状态。

    - messages 使用 Annotated[list, operator.add] 声明 reducer：
      各节点返回的 messages 会被「追加合并」而不是覆盖，
      这就是 LangGraph 的状态归约（reducer）机制，
      天然形成一条完整的运行日志链。
    - 其余字段为「last value」语义：后写覆盖。
    """

    resume_text: str
    jd_text: str
    analysis_result: str
    rewrite_result: str
    score: int
    messages: Annotated[List[str], operator.add]


def build_graph() -> StateGraph:
    """构建并编译简历优化 Agent 的 StateGraph。

    流程（MVP 单向 DAG，无循环）：
        START → parse_node → analyze_node → rewrite_node → format_node → END

    扩展预留：
        - 在 analyze 之后加 conditional edge，score < 60 时回到 rewrite_node 重试；
        - 在 rewrite 之后加人审节点，interrupt 等待用户确认后再继续。
        这些改动都只需操作图结构，节点函数本身不用动。
    """
    graph = StateGraph(AgentState)

    # 注册节点：名字 → 节点函数
    graph.add_node("parse_node", parse_node)
    graph.add_node("analyze_node", analyze_node)
    graph.add_node("rewrite_node", rewrite_node)
    graph.add_node("format_node", format_node)

    # 设置入口
    graph.set_entry_point("parse_node")

    # 单向顺序边：每个节点执行完流向下一个节点
    graph.add_edge("parse_node", "analyze_node")
    graph.add_edge("analyze_node", "rewrite_node")
    graph.add_edge("rewrite_node", "format_node")
    graph.add_edge("format_node", END)

    # compile() 产出可执行的 Runnable，支持 .invoke()/.stream() 等
    return graph.compile()


# 模块级编译一次，FastAPI 应用直接复用该实例（StateGraph 编译开销不大，
# 但作为单例可避免每次请求重复构建）
resume_agent = build_graph()
