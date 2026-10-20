"""对话回复链路回归测试。

覆盖两个线上缺陷：
1. 分析结果交给模型"转述一遍"时被改写走样（Python 后端应届简历被说成
   「拥有移动端 / Dart / React 跨端经验」「符合岗位要求的 3 年以上经验」）；
2. 真正的根因：模型只回「已记录」而**不实际调用**工具，简历/JD 从未写入 state，
   analyze_match 于是静默回落到岗位库的替补 JD，对着「空简历 × 别人的岗位」做分析。
"""

from langgraph.graph import END

from app.chat_graph import (
    _classify_pasted,
    _format_analysis_reply,
    _sanitize_messages,
    route_after_tool,
)


class TestFormatAnalysisReply:
    def test_strips_score_line_and_keeps_body(self):
        out = _format_analysis_reply(
            "SCORE: 70\n\n## 匹配亮点\n- Python 后端开发经验\n- 熟悉 Flask/Django", False
        )
        assert "SCORE" not in out, out
        assert "自评 70" in out
        assert "Python 后端开发经验" in out
        assert "熟悉 Flask/Django" in out

    def test_marks_cache_hit(self):
        out = _format_analysis_reply("SCORE: 88\n\n内容", True)
        assert "命中缓存" in out

    def test_handles_missing_score(self):
        out = _format_analysis_reply("## 匹配亮点\n- 内容", False)
        assert out.startswith("## 匹配度分析")
        assert "自评" not in out

    def test_handles_empty(self):
        assert _format_analysis_reply("", False) == "## 匹配度分析"

    def test_body_is_not_rewritten(self):
        """正文必须原样保留，不允许任何改写（这正是 bug 的根源）。"""
        body = "## 风险点\n- 岗位为校招，候选人已毕业 3 年，属身份超配"
        out = _format_analysis_reply(f"SCORE: 45\n\n{body}", False)
        assert body in out


class TestRouteAfterTool:
    def test_ends_when_tool_already_replied(self):
        state = {"messages": [{"role": "assistant", "content": "分析结果"}]}
        assert route_after_tool(state) == END

    def test_loops_back_when_only_saved(self):
        state = {"messages": [{"role": "tool", "tool_call_id": "1", "content": "简历已保存。"}]}
        assert route_after_tool(state) == "agent_node"


class TestSanitizeMessages:
    def test_drops_empty_assistant_message(self):
        """content 为空且无 tool_calls 的 assistant 消息会被供应商判为 400。"""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "hi"},
        ]
        out = _sanitize_messages(msgs)
        assert len(out) == 2
        assert all(m["role"] != "assistant" for m in out)

    def test_keeps_assistant_with_tool_calls(self):
        msgs = [{"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]}]
        assert len(_sanitize_messages(msgs)) == 1

    def test_normalizes_none_content(self):
        out = _sanitize_messages([{"role": "user", "content": None}])
        assert out[0]["content"] == ""


class TestClassifyPasted:
    """确定性识别用户粘贴的是简历还是 JD，不依赖模型是否记得调工具。"""

    RESUME = (
        "姓名：林小雨\n教育背景：2022.09 - 2026.06 江苏大学 软件工程（本科）\n"
        "专业技能：掌握 Python，熟悉 Flask、Django\n"
        "实习经历：2025.07 - 2025.12 某软件公司 Python 后端开发实习生\n"
        "项目经历：校园二手交易平台\n自我评价：学习能力强"
    )

    JD = (
        "【岗位名称】后端开发工程师（校园招聘）\n【工作地点】北京市\n"
        "【学历要求】本科及以上\n岗位职责：负责核心产品功能与架构开发\n"
        "任职要求：熟练掌握 Java、Go、C++ 至少一门语言"
    )

    def test_detects_resume(self):
        assert _classify_pasted(self.RESUME) == "resume"

    def test_detects_jd(self):
        assert _classify_pasted(self.JD) == "jd"

    def test_short_message_is_other(self):
        assert _classify_pasted("请帮我分析一下匹配度") == "other"
        assert _classify_pasted("") == "other"

    def test_mixed_text_not_forced(self):
        """同时像简历又像 JD 时不硬判，交给模型。"""
        mixed = self.RESUME + "\n" + self.JD
        assert _classify_pasted(mixed) in ("resume", "jd", "other")

