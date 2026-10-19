"""关键词抽取与工作年限识别的回归测试（纯逻辑，不依赖数据库）。

覆盖两个线上缺陷的修复：
1. JD 关键词里混入「要求/参与/至少/一种/岗位/统招/全日制/2026」等招聘套话，
   使占比 35% 的关键词覆盖率维度实际只衡量了套话重叠度；
2. 简历写「工作年限：3 年」却识别不到（scoring 判为未写明年限），
   而另一处实现又用宽松正则把它解析成 23 年。
"""

import pytest

from app.keywords import extract_jd_keywords, extract_years


class TestExtractYears:
    """工作年限识别：显式声明 > 区间 > 经验表述，且排除 4 位年份。"""

    @pytest.mark.parametrize(
        "text,expected",
        [
            # 简历里最常见的显式写法（原缺陷：识别不到，返回 None）
            ("工作年限：3 年", 3),
            ("工作经验：3年", 3),
            ("工龄 5 年", 5),
            ("从业年限: 4 年", 4),
            # 「N 年(以上)……经验」各类语序
            ("3 年经验", 3),
            ("3年以上工作经验", 3),
            ("3 年以上后端开发经验", 3),
            ("5 年以上地图相关领域工作经验", 5),
            ("经验：4 年", 4),
            ("3 years of experience", 3),
            # 区间取下限（最低门槛语义）
            ("3-5 年经验", 3),
            # 不得把 4 位年份、日期、月数当成工作年限（原缺陷：解析成 23/26 年）
            ("2026 届统招全日制本科及以上", None),
            ("1999-04 出生，工作年限：3 年", 3),
            ("2025 年成立，2026 年业务扩张，要求 3-5 年经验。", 3),
            ("合同期 12 个月，无年限要求", None),
            ("", None),
        ],
    )
    def test_detect(self, text, expected):
        assert extract_years(text) == expected

    def test_range_pick_max(self):
        """区间在经验归级场景下取上限（沿用 matching 原有语义）。"""
        assert extract_years("3-5 年经验", pick="max") == 5
        assert extract_years("3-5 年经验", pick="first") == 3

    def test_fresh_graduate_falls_back_to_zero(self):
        """明确写应届且全文无年限数字时，兜底为 0 年而不是 None。"""
        assert extract_years("2026 届应届毕业生，正在找工作") == 0

    def test_consistent_between_modules(self):
        """scoring 与 matching 必须用同一套识别规则，不能再各说各话。"""
        from app import matching, scoring

        resume = "姓名：张三\n工作年限：3 年\n3 年 Python 后端开发经验"
        assert scoring._extract_years(resume) == 3
        assert matching._extract_years(resume) == 3


class TestExtractJdKeywords:
    """JD 关键词抽取：只留真技能，不留招聘套话。"""

    BOILERPLATE = [
        "要求", "参与", "至少", "一种", "岗位", "统招", "全日制", "2026",
        "工作", "使用", "习惯", "效率", "文档", "问题", "团队", "本科", "学历",
    ]

    JD = (
        "【岗位名称】Python 开发工程师\n【学历要求】2026 届统招全日制本科及以上\n"
        "岗位职责：参与公司核心业务系统的需求分析与功能设计，完成 Python 后端模块的开发与联调；"
        "负责代码编写、单元测试及文档维护，保障功能模块的质量与可维护性；"
        "协助优化系统性能，解决开发过程中的技术问题。\n"
        "任职要求：掌握 Python 开发语言；熟练使用 Numpy、Pandas、Matplotlib 等数据处理工具；"
        "至少熟悉 Django / Flask 框架中的一种；至少了解并使用过 Zookeeper、RabbitMQ、Kafka 等中间件中的一种；"
        "有良好的编码习惯和技术文档记录习惯，善于总结。"
    )

    def test_no_boilerplate_keywords(self):
        kws = extract_jd_keywords(self.JD)
        leaked = [k for k in kws if k in self.BOILERPLATE]
        assert not leaked, f"招聘套话被当成了技能关键词：{leaked}"

    def test_real_skills_are_kept(self):
        kws = extract_jd_keywords(self.JD)
        for skill in ("python", "django", "flask", "kafka", "rabbitmq", "zookeeper"):
            assert skill in kws, f"真技能被漏掉了：{skill}（实际：{kws}）"

    def test_salary_and_year_tokens_dropped(self):
        kws = extract_jd_keywords("【薪资范围】15K-25K/月　【要求】3 年以上经验")
        assert not [k for k in kws if k[0].isdigit()], f"数字串混入：{kws}"

    def test_skills_field_takes_priority(self):
        """岗位库自带结构化 skills 时直接采用，不再从正文猜。"""
        assert extract_jd_keywords("随便一段不含技能的文字", ["Python", "Redis"]) == ["Python", "Redis"]

    def test_empty_input(self):
        assert extract_jd_keywords("") == []
