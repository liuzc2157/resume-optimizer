"""关键词抽取与工作年限识别的回归测试（纯逻辑，不依赖数据库）。

覆盖两个线上缺陷的修复：
1. JD 关键词里混入「要求/参与/至少/一种/岗位/统招/全日制/2026」等招聘套话，
   使占比 35% 的关键词覆盖率维度实际只衡量了套话重叠度；
2. 简历写「工作年限：3 年」却识别不到（scoring 判为未写明年限），
   而另一处实现又用宽松正则把它解析成 23 年。
"""

import pytest

from app.keywords import extract_jd_keywords, extract_years, is_fresh_graduate_requirement


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


class TestFreshGraduateRequirement:
    """校招/应届岗识别：这类岗位不能只拿年限比大小。"""

    @pytest.mark.parametrize("jd", [
        "2026 届统招全日制本科及以上学历",
        "2026届校园招聘，计算机相关专业",
        "本岗位面向应届生，欢迎投递",
        "校园招聘 · 管培生方向",
    ])
    def test_detected(self, jd):
        assert is_fresh_graduate_requirement(jd) is True

    @pytest.mark.parametrize("jd", [
        "社会招聘：5 年以上后端开发经验",
        "不限应届，工作经验不限",
        "本岗位面向往届生，需 3 年经验",
        "",
    ])
    def test_not_detected(self, jd):
        assert is_fresh_graduate_requirement(jd) is False


class TestExperienceScoringForCampusJobs:
    """3 年社招经验投 2026 届校招岗，不能拿「年限达标」满分。"""

    CAMPUS_JD = ("【岗位名称】Python 开发工程师（2026 届校园招聘）\n"
                 "【学历要求】2026 届统招全日制本科及以上\n"
                 "任职要求：掌握 Python 开发语言；至少熟悉 Django/Flask 框架中的一种。")

    SOCIAL_JD = ("【岗位名称】资深后端架构师（社会招聘）\n"
                 "任职要求：5 年以上后端经验，熟悉高并发分布式系统。")

    def _exp(self, resume, jd):
        from app.scoring import _score_experience

        score, detail = _score_experience(resume, jd, None)
        return score, detail

    def test_three_years_against_campus_jd_is_over_qualified(self):
        score, detail = self._exp("工作年限：3 年\n3 年 Python 后端开发经验", self.CAMPUS_JD)
        assert score == 45.0, f"应判为身份超配并扣分，实际 {score}（{detail}）"
        assert "应届" in detail or "校招" in detail
        assert "身份超配" in detail

    def test_fresh_grad_against_campus_jd_is_full_marks(self):
        score, detail = self._exp("2026 届应届毕业生，软件工程本科", self.CAMPUS_JD)
        assert score == 100.0, detail

    def test_senior_against_social_jd_unaffected(self):
        score, detail = self._exp("工作年限：8 年\n8 年分布式后端经验", self.SOCIAL_JD)
        assert score == 100.0, detail
        assert "身份超配" not in detail

    def test_explicit_years_in_jd_win_over_campus_signal(self):
        """JD 明确写了年限时，以年限为准，不走校招判定。"""
        jd = "2026 届校园招聘，但要求 5 年以上经验"
        score, detail = self._exp("工作年限：3 年", jd)
        assert score == 50.0, detail  # 3 年 vs 要求 5 年，差 2 年 -> 100-50

