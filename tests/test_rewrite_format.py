"""改写输出的 Markdown 规整测试。

线上现象：模型输出的「优化后的完整简历」在页面上被挤成一整段，
形如「教育背景 · 江苏大学 软件工程（本科）2022.09 - 2026.06 · 主修课程：…」。
根因是模型用「·」当项目符号——它不是 Markdown 列表标记，
而单个换行在同段落内会被渲染成空格，于是所有要点连成一行。
"""

from app.nodes import _normalize_markdown


class TestNormalizeMarkdown:
    def test_fake_bullets_become_markdown_list(self):
        raw = "## 专业技能\n· 编程语言：Python\n· 数据库：MySQL"
        out = _normalize_markdown(raw)
        assert "- 编程语言：Python" in out
        assert "- 数据库：MySQL" in out
        assert "·" not in out

    def test_other_bullet_chars_converted(self):
        raw = "## 教育背景\n• 江苏大学\n・软件工程"
        out = _normalize_markdown(raw)
        assert out.count("- ") == 2
        assert "•" not in out and "・" not in out

    def test_separator_lines_removed(self):
        raw = "## 教育背景\n- 内容\n────────\n=====\n----\n## 专业技能\n- 内容"
        out = _normalize_markdown(raw)
        forsep = ["────", "====", "----"]
        for s in forsep:
            assert s not in out, f"分隔线 {s} 未清除"
        assert out.count("## ") == 2

    def test_heading_gets_blank_line_before_it(self):
        raw = "- 上一段内容\n## 专业技能\n- 技能"
        out = _normalize_markdown(raw)
        assert "- 上一段内容\n\n## 专业技能" in out

    def test_collapses_multiple_blank_lines(self):
        raw = "## A\n\n\n\n- x"
        assert "\n\n\n" not in _normalize_markdown(raw)

    def test_keeps_nested_indent(self):
        raw = "1. 建议\n   · 位置：专业技能\n   · 改后：精通 Python"
        out = _normalize_markdown(raw)
        assert "   - 位置：专业技能" in out

    def test_empty_input(self):
        assert _normalize_markdown("") == ""
