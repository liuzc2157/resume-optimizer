"""派生指标（岗位匹配度 / 简历质量）回归测试。

背景：ATS 总分里只有「关键词 35% + 经验 20%」与目标 JD 有关，另外 45%
（量化 15% + 动词 10% + 结构 20%）衡量的是简历自身质量。结果是一份强简历
投错岗位，总分依然能到 60~71，看不出「投错了」。
因此新增 job_fit（与 JD 相关的部分）与 resume_quality（与 JD 无关的部分），
总分口径保持不变。
"""

from app.scoring import WEIGHTS, score_resume

# 一份「简历本身很强、但方向完全不对」的简历：
# 中文板块齐全、量化多、动词强 —— 所以总分会被简历质量托高，但技能方向不匹配。
STRONG_WRONG_DIRECTION = """姓名：李四
工作年限：4 年
教育背景：本科 计算机科学与技术
专业技能：JavaScript、TypeScript、React、Vue、Webpack、CSS、Node.js
工作经历：
- 主导前端性能优化专项，首屏加载从 3.2s 降低到 1.1s，降幅 66%；
- 搭建并推动组件库落地，组件复用率从 30% 提升到 70%，新页面开发周期缩短 60%；
- 设计并实现数据大屏，支撑 50 个图表同时渲染不掉帧；
项目经历：负责中后台系统重构，打包体积减少 42%，线上缺陷率下降 60%。
自我评价：熟悉前端工程化与性能优化，注重代码可维护性。
"""

# 一份「方向对口」的简历：板块齐全、有量化、有行动动词
MATCHED_RESUME = """姓名：张三
工作年限：3 年
教育背景：本科 计算机科学与技术
专业技能：Python、FastAPI、MySQL、Redis、Kafka、Docker
工作经历：
- 主导订单服务重构，QPS 从 800 提升到 3000；
- 优化慢查询 37 条，P99 从 2.1s 降低到 120ms；
项目经历：负责推荐数据服务，接口耗时降低 62%。
自我评价：熟悉后端开发全流程。
"""

JD = """【岗位名称】Python 后端开发工程师
岗位职责：负责核心业务系统后端开发。
任职要求：熟练掌握 Python、FastAPI、MySQL、Redis、Kafka，熟悉 Docker，3 年以上经验。"""


class TestDerivedMetrics:
    def test_keys_present(self):
        res = score_resume(MATCHED_RESUME, JD)
        assert "job_fit" in res and "resume_quality" in res
        assert 0 <= res["job_fit"] <= 100
        assert 0 <= res["resume_quality"] <= 100

    def test_job_fit_formula(self):
        res = score_resume(MATCHED_RESUME, JD)
        dims = {d["key"]: d for d in res["dimensions"]}
        expect = round(0.7 * dims["keyword_match"]["score"] + 0.3 * dims["experience_match"]["score"])
        assert res["job_fit"] == expect

    def test_resume_quality_formula(self):
        res = score_resume(MATCHED_RESUME, JD)
        dims = {d["key"]: d for d in res["dimensions"]}
        q = {"quantification", "action_verbs", "completeness"}
        total_w = sum(WEIGHTS[k] for k in q)
        expect = round(sum(dims[k]["score"] * WEIGHTS[k] for k in q) / total_w)
        assert res["resume_quality"] == expect

    def test_total_weights_unchanged(self):
        """总分口径不能被改动：仍是 35/20/15/10/20 加权。"""
        assert WEIGHTS == {
            "keyword_match": 0.35,
            "experience_match": 0.20,
            "quantification": 0.15,
            "action_verbs": 0.10,
            "completeness": 0.20,
        }
        res = score_resume(MATCHED_RESUME, JD)
        dims = {d["key"]: d for d in res["dimensions"]}
        expect = round(sum(dims[k]["score"] * w for k, w in WEIGHTS.items()))
        assert res["total"] == expect

    def test_strong_resume_wrong_direction(self):
        """强简历投错方向：总分被质量分托高，但岗位匹配度必须明显更低。"""
        res = score_resume(STRONG_WRONG_DIRECTION, JD)
        assert res["resume_quality"] >= 50, "这份简历本身质量不低，质量分应偏高"
        assert res["total"] > res["job_fit"] + 15, (
            f"总分 {res['total']} 与匹配度 {res['job_fit']} 应明显拉开"
        )
        assert res["job_fit"] < 45, f"方向不匹配时 job_fit 应低于 45，实际 {res['job_fit']}"

    def test_matched_resume_has_high_fit(self):
        res = score_resume(MATCHED_RESUME, JD)
        assert res["job_fit"] >= 60, f"对口时 job_fit 应 ≥60，实际 {res['job_fit']}"
