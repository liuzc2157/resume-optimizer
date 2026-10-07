"""示例 JD 岗位库种子数据（仅技术岗位方向）。

内容依据 2026 年国内主流招聘公开信息整理撰写，覆盖常见技术职业方向 × 经验等级，
用于「智能匹配」冷启动：用户没带 JD 时，系统能从这里挑出最合适的岗位。

说明
----
- 这些是**通用模板**，不是某家公司的真实 JD，用于演示与匹配；
  用户自己传入的 JD 会被单独保存（source=user）并可编辑。
- 每条尽量写全「职责 + 任职要求 + 技能标签」，技能标签直接参与匹配打分。
- 入库按 title 幂等：已存在的不会重复写入，新增条目会自动补齐。
- 本文件仅保留技术类岗位（后端/前端/算法/AI/数据/测试/运维/移动端/安全），
  非技术方向（产品/运营/设计/项目管理/财务/人力/行政）已移除。
  其中 AI = 大模型 / 智能体 / RAG / 多模态等人工智能细分方向。
"""

SEED_JDS: list[dict] = [
    # ---------------- 后端 ----------------
    {
        "position_category": "后端",
        "experience_level": "中级",
        "title": "Python 后端开发工程师（中级）",
        "content": (
            "岗位职责：负责核心业务系统的后端服务设计与开发，使用 Python（FastAPI/Django）"
            "构建高并发 API，基于 MySQL/Redis 做数据存储与缓存，熟悉消息队列与微服务架构。\n"
            "任职要求：1. 本科及以上学历，3 年以上后端开发经验；2. 精通 Python，熟悉 "
            "FastAPI/Flask/Django 至少一个框架；3. 熟练使用 MySQL，具备索引优化与慢查询调优能力；"
            "4. 熟悉 Redis、RabbitMQ/Kafka；5. 有容器化（Docker/K8s）部署经验者优先。"
        ),
        "skills": ["Python", "FastAPI", "MySQL", "Redis", "Docker", "微服务", "消息队列"],
    },
    {
        "position_category": "后端",
        "experience_level": "资深",
        "title": "资深 Golang 后端架构师",
        "content": (
            "岗位职责：主导高并发分布式系统的架构设计，使用 Golang 构建云原生服务，"
            "负责稳定性、性能与可观测性体系建设。\n"
            "任职要求：1. 5 年以上后端经验，3 年以上 Golang 生产经验；2. 深入理解分布式系统、"
            "一致性协议、服务治理；3. 精通 MySQL/TiDB、Redis、Kafka；4. 有大规模高并发系统从 0 到 1 经验；"
            "5. 具备团队技术规划能力。"
        ),
        "skills": ["Golang", "分布式", "MySQL", "TiDB", "Kafka", "云原生", "高并发"],
    },
    {
        "position_category": "后端",
        "experience_level": "中级",
        "title": "Java 后端开发工程师（中级）",
        "content": (
            "岗位职责：参与企业级业务系统开发，基于 Spring Boot / Spring Cloud 微服务体系"
            "实现业务逻辑，保障接口性能与稳定性。\n"
            "任职要求：1. 3 年以上 Java 开发经验；2. 扎实的 Java 基础，熟悉 JVM 调优、并发编程；"
            "3. 熟练使用 Spring Boot、MyBatis、Spring Cloud；4. 熟悉 MySQL、Redis、RocketMQ；"
            "5. 了解分布式事务、限流熔断等微服务治理方案。"
        ),
        "skills": ["Java", "Spring Boot", "Spring Cloud", "MyBatis", "MySQL", "Redis", "JVM"],
    },
    {
        "position_category": "后端",
        "experience_level": "高级",
        "title": "高级 Java 后端工程师（电商方向）",
        "content": (
            "岗位职责：负责电商交易/订单/库存等核心链路的架构与开发，支撑大促高并发场景。\n"
            "任职要求：1. 5 年以上 Java 经验，有电商或高并发交易系统经验；2. 精通分布式架构、"
            "缓存设计、分库分表；3. 熟悉 DDD 领域建模；4. 有秒杀、库存扣减、一致性保障实战经验；"
            "5. 具备性能调优与线上故障排查能力。"
        ),
        "skills": ["Java", "分布式", "高并发", "DDD", "分库分表", "MySQL", "Redis"],
    },
    # ---------------- 前端 ----------------
    {
        "position_category": "前端",
        "experience_level": "初级",
        "title": "前端开发工程师（初级）",
        "content": (
            "岗位职责：参与 Web 前端页面开发与维护，基于 React/Vue 实现交互效果。\n"
            "任职要求：1. 计算机相关专业，1 年以上前端经验或优秀应届生；2. 扎实的 HTML/CSS/JavaScript 基础；"
            "3. 熟悉 React 或 Vue 至少一个框架；4. 了解 TypeScript、Webpack；5. 良好的学习与沟通能力。"
        ),
        "skills": ["JavaScript", "React", "Vue", "TypeScript", "HTML", "CSS"],
    },
    {
        "position_category": "前端",
        "experience_level": "中级",
        "title": "高级前端工程师（React）",
        "content": (
            "岗位职责：负责中后台与 C 端复杂前端应用架构，主导组件库与工程化建设。\n"
            "任职要求：1. 3 年以上前端经验；2. 精通 React 及其生态（Redux/Zustand、Next.js）；"
            "3. 深入 TypeScript、Webpack/Vite 工程化；4. 熟悉前端性能优化与监控；5. 有 Node.js BFF 经验者优先。"
        ),
        "skills": ["React", "TypeScript", "Next.js", "Vite", "性能优化", "Node.js"],
    },
    {
        "position_category": "前端",
        "experience_level": "资深",
        "title": "前端架构师",
        "content": (
            "岗位职责：负责前端整体架构设计、跨端方案选型与研发效能建设，带领团队攻坚技术难题。\n"
            "任职要求：1. 6 年以上前端经验，2 年以上架构或技术负责人经历；2. 深入理解浏览器渲染、"
            "编译构建、微前端；3. 有大型前端项目性能优化与稳定性治理经验；4. 熟悉跨端方案"
            "（React Native / Flutter / 小程序）；5. 优秀的技术抽象与团队影响力。"
        ),
        "skills": ["前端架构", "微前端", "性能优化", "React", "TypeScript", "跨端", "工程化"],
    },
    # ---------------- 算法 ----------------
    {
        "position_category": "算法",
        "experience_level": "高级",
        "title": "机器学习算法专家",
        "content": (
            "岗位职责：负责推荐/搜索方向的算法研发与落地，从特征工程到模型训练部署全链路。\n"
            "任职要求：1. 硕士及以上，3 年以上算法经验；2. 精通 Python，熟悉 PyTorch/TensorFlow；"
            "3. 深入理解推荐系统、搜索排序、CTR 预估；4. 有大规模数据建模与线上 AB 实验经验；"
            "5. 顶会论文或工业级推荐/搜索落地经验优先。"
        ),
        "skills": ["Python", "PyTorch", "推荐系统", "搜索排序", "CTR预估", "AB实验"],
    },
    # ---------------- AI（大模型 / 智能体 / 多模态） ----------------
    {
        "position_category": "AI",
        "experience_level": "中级",
        "title": "大模型应用开发工程师（Agent/RAG）",
        "content": (
            "岗位职责：基于大模型构建企业级 AI 应用，负责 RAG 知识库、Agent 工作流的设计与落地，"
            "持续优化召回质量、回答效果与推理成本。\n"
            "任职要求：1. 1-3 年大模型应用开发实战经验；2. 熟练 Python，精通 LangChain / LangGraph "
            "等 Agent 编排框架；3. 精通 RAG 全流程，熟悉向量数据库（Milvus/FAISS/PGVector）与"
            "文档切分、混合检索、重排序；4. 熟悉主流 LLM API 调用与 Prompt 工程；"
            "5. 掌握 FastAPI 接口开发与 Docker 部署；6. 有 LoRA 微调、模型量化经验者优先。"
        ),
        "skills": ["Python", "LangChain", "LangGraph", "RAG", "向量数据库", "Prompt", "Agent", "FastAPI"],
    },
    {
        "position_category": "AI",
        "experience_level": "高级",
        "title": "NLP / 大模型算法工程师（高级）",
        "content": (
            "岗位职责：负责大模型预训练/微调/对齐与推理优化，推动模型在业务场景的效果与成本平衡。\n"
            "任职要求：1. 硕士及以上，3 年以上 NLP 经验；2. 深入理解 Transformer、注意力机制；"
            "3. 有 SFT/LoRA/DPO 等微调与 RLHF 实战经验；4. 熟悉 vLLM、TensorRT 等推理加速；"
            "5. 有千卡级训练或大模型落地经验优先。"
        ),
        "skills": ["NLP", "大模型", "PyTorch", "LoRA", "微调", "RLHF", "推理优化"],
    },
    {
        "position_category": "AI",
        "experience_level": "中级",
        "title": "AI 应用工程师（多模态 / 智能体方向）",
        "content": (
            "岗位职责：落地多模态大模型与智能体应用，负责图文/语音理解、工具调用与多步任务编排，"
            "把模型能力封装为稳定可用的业务接口。\n"
            "任职要求：1. 2 年以上 AI 应用开发经验；2. 熟练 Python，熟悉主流多模态大模型"
            "（GPT-4V / Qwen-VL / Claude 等）与 Function Calling / MCP 协议；"
            "3. 掌握 Agent 规划-执行-反思框架与任务调度；4. 了解向量检索与 RAG；"
            "5. 有 FastAPI / 微服务开发与线上部署经验。"
        ),
        "skills": ["Python", "多模态", "Agent", "MCP", "Function Calling", "RAG", "FastAPI"],
    },
    {
        "position_category": "AI",
        "experience_level": "高级",
        "title": "大模型推理优化工程师（高级）",
        "content": (
            "岗位职责：负责大模型推理服务的性能与成本优化，涵盖量化、蒸馏、推理引擎与高并发部署。\n"
            "任职要求：1. 5 年以上工程经验，2 年以上大模型推理优化经验；2. 精通 vLLM / TensorRT-LLM "
            "/ SGLang 等推理框架；3. 熟悉 INT8/FP8 量化、KV-Cache 优化、投机解码；"
            "4. 有 GPU 集群调度与算子优化经验；5. 能平衡吞吐、延迟与显存成本。"
        ),
        "skills": ["大模型", "vLLM", "TensorRT", "量化", "KV-Cache", "GPU", "推理优化"],
    },
    # ---------------- 数据 ----------------
    {
        "position_category": "数据",
        "experience_level": "中级",
        "title": "数据分析师（中级）",
        "content": (
            "岗位职责：负责业务数据分析、指标体系搭建与可视化看板，输出决策支持报告。\n"
            "任职要求：1. 3 年以上数据分析经验；2. 精通 SQL，熟悉 Hive/Spark；"
            "3. 熟练使用 Python（pandas）做数据处理；4. 熟悉 Tableau/BI 工具；5. 具备业务敏感度与数据叙事能力。"
        ),
        "skills": ["SQL", "Python", "pandas", "Hive", "BI", "数据可视化"],
    },
    {
        "position_category": "数据",
        "experience_level": "中级",
        "title": "数据仓库开发工程师",
        "content": (
            "岗位职责：负责数据仓库分层建模与 ETL 开发，保障数据质量与产出时效。\n"
            "任职要求：1. 3 年以上数仓经验；2. 精通 SQL 与维度建模（Kimball）；"
            "3. 熟练使用 Hive / Spark / Flink；4. 熟悉调度系统（Airflow/DolphinScheduler）；"
            "5. 有数据治理、血缘与质量监控经验优先。"
        ),
        "skills": ["SQL", "数仓", "Hive", "Spark", "Flink", "ETL", "维度建模"],
    },
    {
        "position_category": "数据",
        "experience_level": "高级",
        "title": "数据平台工程师（高级）",
        "content": (
            "岗位职责：负责大数据平台与实时计算链路建设，支撑 PB 级数据的存储、计算与服务化。\n"
            "任职要求：1. 5 年以上大数据开发经验；2. 精通 Spark/Flink 实时与离线计算；"
            "3. 熟悉 HDFS、Hive、Iceberg/Hudi、ClickHouse/Doris；4. 有集群调优与成本控制经验；"
            "5. 具备平台化思维与工程化能力。"
        ),
        "skills": ["Spark", "Flink", "ClickHouse", "Doris", "Iceberg", "大数据平台", "实时计算"],
    },
    # ---------------- 测试 ----------------
    {
        "position_category": "测试",
        "experience_level": "中级",
        "title": "自动化测试工程师",
        "content": (
            "岗位职责：负责服务端/前端自动化测试体系建设，编写接口与 UI 自动化用例。\n"
            "任职要求：1. 3 年以上测试经验；2. 熟悉 Python/Java 任一，会 pytest/Selenium；"
            "3. 熟悉接口测试、性能测试（JMeter）；4. 了解 CI/CD 与 Docker；5. 有质量保障体系建设经验优先。"
        ),
        "skills": ["Python", "pytest", "Selenium", "JMeter", "接口测试", "CI/CD"],
    },
    {
        "position_category": "测试",
        "experience_level": "高级",
        "title": "测试开发工程师（SDET）",
        "content": (
            "岗位职责：研发测试工具与质量平台，建设自动化测试、压测、监控与引流回放能力。\n"
            "任职要求：1. 4 年以上测试开发经验；2. 扎实的编码能力（Python/Go/Java 任一）；"
            "3. 有测试平台、Mock 服务、流量回放等工具研发经验；4. 熟悉性能压测与全链路监控；"
            "5. 能推动研发流程与质量度量改进。"
        ),
        "skills": ["测试开发", "Python", "自动化测试", "压测", "质量平台", "Go"],
    },
    # ---------------- 运维 / DevOps ----------------
    {
        "position_category": "运维",
        "experience_level": "中级",
        "title": "运维开发工程师（DevOps）",
        "content": (
            "岗位职责：负责 CI/CD 流水线与自动化运维平台建设，保障服务发布效率与稳定性。\n"
            "任职要求：1. 3 年以上运维/DevOps 经验；2. 熟练 Linux 与 Shell/Python；"
            "3. 精通 Docker、Kubernetes；4. 熟悉 Jenkins/GitLab CI、ArgoCD；"
            "5. 有监控体系（Prometheus/Grafana）建设经验。"
        ),
        "skills": ["Linux", "Docker", "Kubernetes", "CI/CD", "Prometheus", "Python"],
    },
    {
        "position_category": "运维",
        "experience_level": "高级",
        "title": "SRE 高级工程师",
        "content": (
            "岗位职责：负责大规模分布式系统的稳定性建设，包括容量规划、故障演练、应急预案与 SLO 治理。\n"
            "任职要求：1. 5 年以上 SRE/运维经验；2. 深入理解 K8s 与云原生生态；"
            "3. 熟悉可观测性三大支柱（Metrics/Log/Trace）；4. 有混沌工程与故障复盘实践；"
            "5. 具备较强的问题定位与自动化能力。"
        ),
        "skills": ["SRE", "Kubernetes", "云原生", "可观测性", "混沌工程", "SLO"],
    },
    # ---------------- 移动端 ----------------
    {
        "position_category": "移动端",
        "experience_level": "中级",
        "title": "Android 开发工程师（中级）",
        "content": (
            "岗位职责：负责 Android 客户端功能开发与性能优化。\n"
            "任职要求：1. 3 年以上 Android 开发经验；2. 精通 Kotlin/Java，熟悉 Android SDK；"
            "3. 熟悉 Jetpack Compose、协程、MVVM；4. 有性能优化、内存泄漏排查经验；"
            "5. 了解组件化与热修复优先。"
        ),
        "skills": ["Android", "Kotlin", "Java", "Jetpack", "性能优化", "MVVM"],
    },
    {
        "position_category": "移动端",
        "experience_level": "中级",
        "title": "跨端开发工程师（Flutter/React Native）",
        "content": (
            "岗位职责：负责跨端 App 研发，实现一套代码多端复用并保障原生体验。\n"
            "任职要求：1. 3 年以上移动端经验，1 年以上 Flutter 或 RN 实战；2. 熟悉 Dart 或 React 生态；"
            "3. 了解原生（Android/iOS）桥接与性能调优；4. 有跨端组件库或工程化经验优先。"
        ),
        "skills": ["Flutter", "React Native", "Dart", "跨端", "Android", "iOS"],
    },
    # ---------------- 安全 ----------------
    {
        "position_category": "安全",
        "experience_level": "中级",
        "title": "信息安全工程师",
        "content": (
            "岗位职责：负责应用安全、漏洞治理与安全防护体系建设，参与安全评审与应急响应。\n"
            "任职要求：1. 3 年以上安全经验；2. 熟悉 OWASP Top 10 与常见漏洞原理；"
            "3. 掌握渗透测试流程与常用工具；4. 了解等保 2.0 与数据安全合规；"
            "5. 有代码审计或安全开发（DevSecOps）经验优先。"
        ),
        "skills": ["应用安全", "渗透测试", "漏洞治理", "代码审计", "等保", "DevSecOps"],
    },

    # ---------------- 应届生 / 实习（技术方向） ----------------
    {
        "position_category": "后端",
        "experience_level": "初级",
        "title": "后端开发实习生（校招）",
        "content": (
            "岗位职责：参与业务系统后端模块开发，在导师指导下完成需求实现、接口联调与问题修复。\n"
            "任职要求：1. 本科及以上在读，计算机相关专业，每周可到岗 4 天以上；"
            "2. 掌握一门后端语言（Java/Python/Go 任一），了解常用数据结构与算法；"
            "3. 了解 MySQL 基本使用与 SQL 编写；4. 了解 Git 与 Linux 常用命令；"
            "5. 有个人项目、开源贡献或竞赛经历优先；6. 学习能力强，沟通顺畅。"
        ),
        "skills": ["Java", "Python", "Go", "MySQL", "数据结构", "Git", "应届生"],
    },
    {
        "position_category": "后端",
        "experience_level": "初级",
        "title": "Java 后端开发工程师（应届）",
        "content": (
            "岗位职责：负责业务系统功能开发与维护，参与需求评审、编码实现与线上问题排查。\n"
            "任职要求：1. 本科及以上应届毕业生，计算机相关专业；2. 扎实的 Java 基础，"
            "理解面向对象与常用集合；3. 了解 Spring Boot、MyBatis 等主流框架；"
            "4. 熟悉 MySQL 与基本 SQL 优化；5. 有实习或项目经历优先；6. 具备良好的学习能力与责任心。"
        ),
        "skills": ["Java", "Spring Boot", "MyBatis", "MySQL", "SQL", "应届生"],
    },
    {
        "position_category": "前端",
        "experience_level": "初级",
        "title": "前端开发实习生（校招）",
        "content": (
            "岗位职责：参与 Web 页面开发与组件维护，配合完成交互实现与样式还原。\n"
            "任职要求：1. 本科及以上在读，每周可到岗 4 天以上；2. 掌握 HTML/CSS/JavaScript 基础；"
            "3. 了解 React 或 Vue 框架；4. 了解 HTTP、浏览器渲染基本原理；"
            "5. 有个人作品、博客或课程项目优先；6. 对交互细节有追求。"
        ),
        "skills": ["HTML", "CSS", "JavaScript", "React", "Vue", "应届生"],
    },
    {
        "position_category": "算法",
        "experience_level": "初级",
        "title": "算法工程师（应届/实习）",
        "content": (
            "岗位职责：参与推荐、搜索或 NLP 方向的算法研究与工程实现，协助完成实验与效果评估。\n"
            "任职要求：1. 硕士在读或应届，计算机/数学/统计相关专业；2. 扎实的机器学习基础，"
            "熟悉常见模型与评估指标；3. 熟练 Python，了解 PyTorch 或 TensorFlow；"
            "4. 有 Kaggle、天池等竞赛或论文、项目经历优先；5. 具备良好的数学功底与工程能力。"
        ),
        "skills": ["机器学习", "Python", "PyTorch", "深度学习", "NLP", "应届生"],
    },
    {
        "position_category": "数据",
        "experience_level": "初级",
        "title": "数据分析师（应届）",
        "content": (
            "岗位职责：负责业务数据提取、整理与基础分析，产出日报周报与专题分析。\n"
            "任职要求：1. 本科及以上应届，统计/数学/计算机/经济相关专业；2. 熟练 SQL，"
            "能独立完成数据提取与清洗；3. 会用 Excel/Python 做数据处理与可视化；"
            "4. 对业务有好奇心，逻辑清晰；5. 有数据分析实习或项目经历优先。"
        ),
        "skills": ["SQL", "Excel", "Python", "数据分析", "数据可视化", "应届生"],
    },
    {
        "position_category": "测试",
        "experience_level": "初级",
        "title": "测试工程师（应届）",
        "content": (
            "岗位职责：负责功能测试与用例执行，协助定位与跟踪缺陷，参与自动化测试建设。\n"
            "任职要求：1. 本科及以上应届，计算机相关专业；2. 了解软件测试流程与常用方法；"
            "3. 会编写测试用例，了解缺陷管理流程；4. 了解 Python 或 Java，有自动化脚本经验优先；"
            "5. 细心耐心，具备良好的沟通与问题追踪能力。"
        ),
        "skills": ["功能测试", "测试用例", "缺陷管理", "Python", "自动化测试", "应届生"],
    },
]
