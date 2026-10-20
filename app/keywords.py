"""JD 关键词抽取 + 工作年限识别（scoring 与 matching 共用）。

为什么单独抽一个模块
--------------------
这两件事原来在 `scoring.py` 和 `matching.py` 里各写了一份，规则还不一样，导致两类线上问题：

1. **工作年限识别不到**（scoring 那份）：
   只认「3 年经验」这种固定语序，而简历里最常见的是「工作年限：3 年」「工作经验 3 年」，
   于是简历明明写了年限，却被判成「未写明年限」，经验维度退化成中性分。

2. **年限被算成天文数字**（matching 那份）：
   用 `\\d{1,2} 年` 这种宽松匹配取最大值，会把「1999-04」「2026 届」之类的片段也吃进来，
   一份 3 年经验的简历能被解析成 23 年，进而被误判成「资深」。

3. **JD 关键词全是招聘套话**（scoring 那份）：
   直接对 JD 做分词后按词频取 Top-20，没有停用词也没有技术词过滤，
   于是「要求、参与、至少、一种、岗位、统招、全日制、2026」这些词全被当成「技能关键词」，
   关键词覆盖率维度（占总分 35%）实际衡量的是套话重叠度，分数没有意义。

本模块把这两件事收口成一份实现：关键词走「技术词表 + 英文标识符」优先，年限走
「显式声明 > 经验表述 > 区间」的优先级，并统一排除 4 位年份。
"""

import re
import unicodedata
from typing import Iterable, List, Optional

from .embedding import normalize

# ======================================================================================
# 一、工作年限识别
# ======================================================================================
# 说明：所有数字都加 `(?<![\d.])` 左界断言，保证「1999」「2026.07」这类 4 位年份
# 或小数不会被误当成年限；「2026 年」中的 26 也无法命中（前面紧邻数字）。
_YEAR_NUM = r"(?<![\d.])(\d{1,2})"

# 年限识别专用的「轻归一化」：只做全角转半角 + 小写 + 压缩空白，
# **保留短横线等区间符号**。不能用 embedding.normalize —— 它会把 "3-5 年"
# 的连字符压成空格，导致区间识别失效、只能取到区间上限。
_YEAR_KEEP_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff\-~～到至+]+")


def _year_text(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = _YEAR_KEEP_RE.sub(" ", t)
    return " ".join(t.split())

# 优先级 1：显式声明，形如「工作年限：3 年」「工作经验 3 年」「工龄 3 年」
_EXPLICIT_YEAR_PATTERNS: List[str] = [
    r"(?:工作年限|工作经验|工龄|从业年限|从业经历|研发年限|开发年限|测试年限|相关年限)"
    r"\s*[：:为]?\s*" + _YEAR_NUM + r"\s*年",
]

# 优先级 2：区间表述，如「3-5 年经验」，配合 pick 取首值(3)或末值(5)
_RANGE_YEAR_PATTERNS: List[str] = [
    _YEAR_NUM + r"\s*[-~～到至]\s*(\d{1,2})\s*年",
]

# 优先级 3：「N 年(以上)……经验」，中间允许最多 12 个汉字
# （覆盖「5 年以上地图相关领域工作经验」「3 年以上云计算或分布式系统相关经验」）
_EXPERIENCE_YEAR_PATTERNS: List[str] = [
    _YEAR_NUM + r"\s*年(?:及)?以上[\u4e00-\u9fff]{0,12}经验",
    _YEAR_NUM + r"\s*年[\u4e00-\u9fff]{0,12}经验",
    r"经验\s*[：:为]?\s*" + _YEAR_NUM + r"\s*年",
    _YEAR_NUM + r"\s*\+?\s*years?(?:\s+of)?\s+(?:relevant\s+)?experience",
]

# 无任何年限数字时，用「应届/在校」兜底为 0 年
_FRESH_GRADUATE_HINTS = ("应届", "在校生", "实习生", "在校")

# ---- 校招 / 应届岗位识别 ----
# 场景：JD 写「2026 届」「校园招聘」，岗位性质就是校招。
# 此时一个已经工作 3 年的候选人，年限上「达标」但身份与薪资预期明显错配，
# 不能按「3 年 ≥ 3 年」给满分——这是简历顾问最该提醒的点之一。
_FRESH_GRAD_JD_PATTERNS = [
    re.compile(r"20\d{2}\s*届"),
    re.compile(r"校园招聘|校招|应届生?|在校生|实习生招聘|管培生|毕业生"),
]
# JD 自身声明面向往届/社招时，不做校招判定
_NOT_FRESH_GRAD_PATTERNS = [
    re.compile(r"不限应届|应届不限|社招|社会招聘|往届"),
]


def is_fresh_graduate_requirement(jd_text: str) -> bool:
    """判断该 JD 是否为「应届 / 校园招聘」性质岗位。"""
    if not jd_text:
        return False
    text = normalize(jd_text)
    if any(p.search(text) for p in _NOT_FRESH_GRAD_PATTERNS):
        return False
    return any(p.search(text) for p in _FRESH_GRAD_JD_PATTERNS)


def _first_int(pattern: str, text: str) -> Optional[int]:
    m = re.search(pattern, text, flags=re.IGNORECASE)
    if not m:
        return None
    for g in m.groups():
        if g and g.isdigit():
            return int(g)
    return None


def extract_years(text: str, pick: str = "first") -> Optional[int]:
    """从简历或 JD 文本中抽取「工作年限」。

    参数
    ----
    text : 原始文本（内部会先归一化）
    pick : 遇到区间（如「3-5 年」）时的取值方式
           - ``"first"`` 取区间下限（默认，代表「最低门槛」语义）
           - ``"max"``   取区间上限（用于经验等级分类，如「3-5 年」判为高级）

    返回
    ----
    整数年限；识别不到时返回 ``None``（调用方据此给中性分，而不是判 0）。
    """
    if not text:
        return None
    norm = _year_text(text)
    plain = normalize(text)

    # 1) 显式声明优先：简历里的「工作年限：3 年」是最权威的信号
    for pat in _EXPLICIT_YEAR_PATTERNS:
        got = _first_int(pat, norm)
        if got is not None:
            return got

    # 2) 区间表述（依赖轻归一化保留的连字符）
    for pat in _RANGE_YEAR_PATTERNS:
        m = re.search(pat, norm, flags=re.IGNORECASE)
        if not m:
            continue
        nums = [int(g) for g in m.groups() if g and g.isdigit()]
        if nums:
            return max(nums) if pick == "max" else min(nums)

    # 3) 「N 年……经验」类表述
    for pat in _EXPERIENCE_YEAR_PATTERNS:
        got = _first_int(pat, norm)
        if got is not None:
            return got

    # 4) 兜底：明确写了应届/在校，且全文没有任何年限数字
    if any(h in plain for h in _FRESH_GRADUATE_HINTS):
        return 0
    return None


# ======================================================================================
# 二、JD 关键词抽取
# ======================================================================================
# 技术词表（全部小写）：命中即视为「真技能」，优先级最高。
# 前半部分汇总自 matching.CATEGORY_KEYWORDS（保持与岗位分类一致的词表），
# 后半部分补充常见技术栈、数据库、中间件、工具与通用工程术语。
TECH_LEXICON: frozenset = frozenset({
    # —— 语言 ——
    "python", "java", "golang", "go", "c++", "c#", "php", "ruby", "rust", "scala",
    "javascript", "typescript", "js", "ts", "kotlin", "swift", "objective-c", "dart",
    "shell", "bash", "sql", "html", "css", "sass", "scss", "less", "r", "matlab",
    # —— 后端框架 / 运行时 ——
    "fastapi", "flask", "django", "spring", "springboot", "spring boot", "spring cloud",
    "mybatis", "hibernate", "express", "koa", "nestjs", "node", "nodejs", "node.js",
    "grpc", "restful", "rest", "api", "graphql", "websocket", "celery", "gin", "beego",
    # —— 前端框架 / 工程化 ——
    "react", "vue", "angular", "angularjs", "next.js", "nextjs", "nuxt", "svelte",
    "webpack", "vite", "rollup", "babel", "eslint", "tailwind", "element ui",
    "antd", "echarts", "redux", "mobx", "vuex", "pinia", "jquery", "小程序", "鸿蒙",
    # —— 数据库 / 存储 ——
    "mysql", "postgresql", "postgres", "oracle", "sqlserver", "sqlite", "mongodb",
    "redis", "elasticsearch", "clickhouse", "tidb", "hbase", "leveldb", "rocksdb",
    "pgvector", "milvus", "faiss", "chroma", "向量数据库",
    # —— 中间件 / 消息队列 ——
    "kafka", "rabbitmq", "rocketmq", "zookeeper", "nacos", "etcd", "pulsar",
    # —— 云原生 / 运维 ——
    "docker", "kubernetes", "k8s", "istio", "helm", "terraform", "ansible", "jenkins",
    "gitlab", "github actions", "ci/cd", "devops", "sre", "prometheus", "grafana",
    "opentelemetry", "skywalking", "nginx", "linux", "ubuntu", "centos", "aws", "azure",
    "阿里云", "腾讯云", "华为云", "云原生", "serverless", "微服务", "高并发", "高可用",
    "负载均衡", "限流", "熔断", "容灾", "多活", "混沌工程", "全链路压测", "架构设计",
    # —— 算法 / AI ——
    "人工智能", "ai", "机器学习", "深度学习", "大模型", "llm", "nlp", "自然语言",
    "cv", "计算机视觉", "多模态", "rag", "检索增强", "agent", "智能体", "微调",
    "lora", "qlora", "sft", "rlhf", "dpo", "transformer", "bert", "gpt", "vit", "clip",
    "embedding", "prompt", "prompt 工程", "思维链", "cot", "预训练", "推理加速",
    "量化", "蒸馏", "模型压缩", "扩散模型", "pytorch", "tensorflow", "paddlepaddle",
    "paddle", "keras", "sklearn", "scikit-learn", "opencv", "huggingface", "megatron",
    "deepspeed", "vllm", "langchain", "llamaindex", "faq", "知识图谱", "推荐系统",
    "搜索", "排序", "数据结构", "算法", "leetcode", "动态规划", "图算法", "最优化",
    # —— 数据 / 分析 ——
    "数据分析", "数据仓库", "数仓", "etl", "hive", "spark", "flink", "mapreduce",
    "bi", "tableau", "power bi", "powerbi", "superset", "metabase", "报表", "指标体系",
    "ab 测试", "a/b 测试", "abtest", "用户画像", "漏斗分析", "留存分析", "rfm",
    "pandas", "numpy", "matplotlib", "seaborn", "jupyter", "excel", "vba", "spss",
    "统计分析", "回归分析", "时间序列", "假设检验", "聚类", "kmeans",
    # —— 测试 ——
    "自动化测试", "接口测试", "性能测试", "单元测试", "集成测试", "pytest", "unittest",
    "selenium", "appium", "jmeter", "postman", "playwright", "cypress", "testng",
    "allure", "mock", "契约测试", "压测", "质量保障", "测试用例", "缺陷管理",
    # —— 地图 / 自动驾驶（垂直领域，容易被分词打散，需显式入表） ——
    "gis", "地图", "导航", "路径规划", "路网", "eta", "自动驾驶", "无人车",
    "l4", "测绘", "感知", "bff",
    # —— 移动端 / 嵌入式 / 安全 ——
    "android", "ios", "flutter", "react native", "unipp", "uni-app", "微信小程序",
    "嵌入式", "单片机", "stm32", "arm", "rtos", "固件", "firmware", "iot", "物联网",
    "安全", "渗透测试", "漏洞", "等保", "攻防", "风控", "加密", "证书",
    # —— 通用工程能力（够具体才保留） ——
    "代码评审", "codereview", "设计模式", "重构", "性能优化", "可观测性", "日志",
    "监控告警", "单元测试覆盖", "敏捷开发", "scrum", "git",
})

# 招聘套话 / 通用词：这些词在任何 JD 里都高频出现，但**不构成技能差异**，
# 即使出现在技术词表之外也一律丢弃。这是本次修复「关键词全是套话」的核心。
GENERIC_STOPWORDS: frozenset = frozenset({
    # 文档结构类
    "岗位", "岗位名称", "岗位职责", "职责", "任职", "要求", "任职要求", "资格", "描述",
    "工作地点", "地点", "地点要求", "学历", "学历要求", "薪资", "薪资范围", "范围",
    "招聘", "招聘人数", "人数", "发布", "发布时间", "截止", "截止时间", "类别",
    "招聘类别", "工作性质", "性质", "月薪", "年薪", "福利", "待遇", "应聘", "投递",
    # 学历 / 身份类
    "本科", "硕士", "专科", "博士", "研究生", "学历本科", "统招", "全日制", "非全日制",
    "应届", "往届", "在校", "毕业生", "届", "专业", "相关专业", "以上学历",
    "计算机", "工程", "科学", "学科", "通信", "电子信息",
    # 专业名称：不是技能，但会在简历里出现，容易被当成长技能命中
    "软件工程", "计算机科学与技术", "网络工程", "电子信息工程", "通信工程",
    # 通用动作 / 描述词
    "参与", "协助", "负责", "配合", "完成", "支持", "提供", "推动", "保障", "跟踪",
    "学习", "使用", "习惯", "整理", "编写", "撰写", "维护", "建设", "设计", "实现",
    "开发", "优化", "提升", "降低", "解决", "分析", "处理", "制定", "落地", "执行",
    "沟通", "协作", "合作", "团队", "部门", "公司", "客户", "业务", "需求", "方案",
    "产品", "项目", "系统", "平台", "应用", "功能", "模块", "代码", "文档", "资料",
    "服务", "规划", "自动", "驾驶", "引擎", "场景", "环节", "阶段", "内容", "信息",
    "研发", "定位", "负责", "参与", "支撑",
    "流程", "规范", "制度", "标准", "目标", "时间", "效率", "质量", "价值", "结果",
    "能力", "经验", "技能", "意识", "态度", "思维", "习惯", "问题", "问题解决",
    "独立", "主动", "积极", "良好", "优秀", "较强", "较强", "较强", "较强",
    "熟悉", "熟练", "掌握", "了解", "精通", "具备", "具有", "能够", "可以", "善于",
    # jieba 会把「熟练掌握」「深入理解」切成一整个词，单词停用表拦不住，需显式列出
    "熟练掌握", "熟练运用", "熟练操作", "深入理解", "深刻理解", "扎实的", "较强的",
    "优先考虑", "工作经验", "项目经验", "相关经验", "学习能力", "沟通能力", "团队协作",
    "至少", "一种", "一项", "以上", "以下", "以及", "或者", "等等", "优先", "加分",
    # 量词碎片：「至少一门语言」里的「一门」会被 jieba 单独切出来
    "一门", "一种", "一项", "一个", "一类", "两种", "三项", "语言",
    "相关", "方向", "领域", "行业", "技术", "技术方案", "新技术", "前沿",
    "一定", "较强", "优秀", "丰富的", "扎实的", "良好的",
})

# 英文停用词（避免把 years / experience 这类词当技能）
_EN_STOPWORDS: frozenset = frozenset({
    "the", "and", "for", "with", "you", "will", "our", "are", "have", "has", "been",
    "years", "year", "experience", "ability", "skills", "skill", "good", "strong",
    "work", "working", "team", "teams", "job", "role", "must", "plus", "etc", "all",
    "use", "using", "new", "other", "more", "any", "can", "not",
})

# 纯数字 / 年份 / 序号类，直接排除
_NOISE_RE = re.compile(r"^\d+$|^\d{4}$|^\d+年$|^\d+月$|^[ivx]+$")


def _is_noise(token: str) -> bool:
    """判断分词结果是否为无意义噪音。"""
    t = token.strip().lower()
    if not t or len(t) < 2:
        return True
    if _NOISE_RE.match(t):
        return True
    # 数字开头的串（如薪资 10k / 15k、年份 2026）不是技术标识符
    if t[0].isdigit():
        return True
    if t in GENERIC_STOPWORDS:
        return True
    return False


def _candidates(text: str) -> List[str]:
    """切词：jieba 中文分词 + 英文标识符抽取（保留 C++/Node.js/Power BI 这类形态）。"""
    norm = normalize(text)
    toks: List[str] = []
    # 英文/数字标识符：归一化后已小写，标点被压成空格，这里按空白切英文串
    toks.extend(re.findall(r"[a-z][a-z0-9+#./\-]{1,}", norm))
    # 中文词：优先 jieba（不可用时退化为 2-4 字滑窗）
    try:  # pragma: no cover - jieba 为可选依赖
        import jieba  # type: ignore

        toks.extend(t for t in jieba.cut(norm) if t.strip())
    except Exception:  # noqa: BLE001
        for seg in re.findall(r"[\u4e00-\u9fff]{2,}", norm):
            toks.append(seg)
            for n in (2, 3):
                toks.extend(seg[i:i + n] for i in range(len(seg) - n + 1))
    return toks


def _rank(tokens: Iterable[str]) -> List[str]:
    """按频次排序并去重（保持首次出现顺序作为次序键）。"""
    freq: dict = {}
    order: dict = {}
    for i, t in enumerate(tokens):
        freq[t] = freq.get(t, 0) + 1
        order.setdefault(t, i)
    return sorted(freq, key=lambda t: (-freq[t], order[t]))


def extract_jd_keywords(
    jd_content: str,
    jd_skills: Optional[List[str]] = None,
    limit: int = 20,
) -> List[str]:
    """从 JD 中抽取用于「关键词覆盖率」的技能关键词。

    分级策略（精度优先，宁缺毋滥）：
      第一级 岗位库自带的结构化 skills 字段（最准，直接返回）
      第二级 命中技术词表的词 + 英文技术标识符（如 python / fastapi / k8s）
      第三级 关键词不足时，用「非套话的中文词」补位（适配词表之外的冷门技能）

    这样既能保证「python / django / mysql」这类真技能排在最前，
    又不会把「要求、参与、至少、一种、统招、全日制」之类的招聘套话算成技能。
    """
    if jd_skills:
        cleaned = [str(s).strip() for s in jd_skills if str(s).strip()]
        if cleaned:
            return cleaned[:limit]
    if not jd_content:
        return []

    ranked = _rank(t for t in _candidates(jd_content) if not _is_noise(t))

    tier1: List[str] = []   # 技术词表命中
    tier2: List[str] = []   # 英文技术标识符
    tier3: List[str] = []   # 其他中文词（补位）
    seen: set = set()
    for t in ranked:
        if t in seen:
            continue
        low = t.lower().strip()
        if low in TECH_LEXICON:
            tier1.append(t)
            seen.add(t)
        elif t.isascii() and re.match(r"^[a-z]", low) and low not in _EN_STOPWORDS and len(low) >= 2:
            tier2.append(t)
            seen.add(t)
        elif re.search(r"[\u4e00-\u9fff]", t) and len(t) >= 2:
            tier3.append(t)
            seen.add(t)

    result = tier1 + tier2
    # 补位门槛不宜太高：硬凑到 10 个会把「编程」「语言」这类弱词拉进来，
    # 反而稀释真正的技能信号，宁可关键词少一点。
    if len(result) < 8:
        result += tier3[: 8 - len(result)]
    return result[:limit]


# ======================================================================================
# 三、并列条件（"至少一门 / 任一 / 等"）识别
# ======================================================================================
# 问题：JD 写「熟练掌握 Java、Go、C++、PHP 等至少一门语言」时，逐项比对会把
# 没写 PHP 判成"缺失"，简历写 Java 也救不回来——这是把"或"关系当成了"与"关系。
# 同理「Windows、Unix、Linux 等主流操作系统」。
# 这里把这类并列项合并成一个**要求单元**：命中任意一项即视为满足。
#
# 注意：只有出现「等 / 至少一门 / 任一 / 之一」才合并；像
# 「熟悉 MySQL、Redis、Kafka」这种没有"等"字的是并列硬要求，不能合并。
_ENUM_ITEM = r"[A-Za-z][A-Za-z0-9+#./]{1,}|[\u4e00-\u9fff]{2,8}"
# 注意：_ENUM_ITEM 本身是「A|B」形式，拼进别的正则时必须整体加非捕获括号，
# 否则 `|` 会把外层分组切成两条分支（实测导致重复部分跳过分隔符去匹配中文）。
_ENUM_TOKEN = r"(?:" + _ENUM_ITEM + r")"
_ENUM_SEP = r"[、,，/]"
_GROUP_TAIL = r"(?:等[^。；\n]{0,12}?(?:至少|一[门种个项])|等|至少[一]?[门种个项]|任一|之一)"

_ENUM_GROUP_RE = re.compile(
    r"(" + _ENUM_TOKEN + r"(?:\s*" + _ENUM_SEP + r"\s*" + _ENUM_TOKEN + r")+)"
    r"\s*" + _GROUP_TAIL
)


def _key(term: str) -> str:
    """比较用的归一化键。"""
    return normalize(term).strip()


def find_requirement_groups(jd_content: str) -> List[List[str]]:
    """从 JD 原文里找出「任一即可」的并列条件组。

    返回示例：``[["Java", "Go", "C++", "PHP"], ["Windows", "Unix", "Linux"]]``
    """
    if not jd_content:
        return []
    groups: List[List[str]] = []
    for m in _ENUM_GROUP_RE.finditer(jd_content):
        raw = m.group(1)
        members = [x.strip() for x in re.split(r"\s*" + _ENUM_SEP + r"\s*", raw) if x.strip()]
        members = [x for x in members if len(x) >= 2 and not x.isdigit()]
        if len(members) >= 2:
            groups.append(members)
    return groups


def requirement_units(jd_content: str, keywords: List[str]) -> List[tuple]:
    """把关键词整理成「要求单元」列表：``[(成员列表, 展示标签), ...]``。

    普通关键词自成一个单元；并列条件组整体成为一个单元（标签形如
    ``Java／Go／C++／PHP（任一）``），满足任意成员即可。
    """
    groups = find_requirement_groups(jd_content)

    # 关键词 -> 所属并列组
    membership: dict = {}
    for gi, members in enumerate(groups):
        for mem in members:
            membership.setdefault(_key(mem), gi)

    units: List[tuple] = []
    used_group: set = set()
    for kw in keywords:
        gi = membership.get(_key(kw))
        if gi is None:
            units.append(([kw], kw))
            continue
        if gi in used_group:
            continue
        used_group.add(gi)
        members = groups[gi]
        label = "／".join(members) + "（任一）"
        units.append((members, label))
    return units
