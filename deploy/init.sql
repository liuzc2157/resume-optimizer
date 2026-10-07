-- ============================================================
-- 简历优化 Agent —— MySQL 初始化脚本
-- 用途：建库建表 + 写入示例 JD 岗位库（含岗位类别/经验等级）
-- 用法：mysql -u <user> -p < init.sql
-- ============================================================

CREATE DATABASE IF NOT EXISTS `resume_optimizer`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE `resume_optimizer`;

-- ---------- 1. JD 岗位库 ----------
-- position_category：岗位类别（后端 / 前端 / 算法 / 数据 / 测试 / 产品 ...）
-- experience_level ：经验等级（初级 / 中级 / 高级 / 资深）
CREATE TABLE IF NOT EXISTS `jds` (
  `id`                INT          NOT NULL AUTO_INCREMENT,
  `position_category` VARCHAR(64)  NOT NULL COMMENT '岗位类别',
  `experience_level`  VARCHAR(32)  NOT NULL COMMENT '经验等级',
  `title`             VARCHAR(255) NOT NULL COMMENT '岗位标题',
  `content`           TEXT         NOT NULL COMMENT 'JD 全文',
  `skills`            JSON         NULL     COMMENT '提取的关键技能标签',
  `created_at`        DATETIME     DEFAULT CURRENT_TIMESTAMP,
  `updated_at`        DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  INDEX `idx_cat_level` (`position_category`, `experience_level`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='JD 岗位库';

-- ---------- 2. 分析结果缓存 ----------
-- cache_key = md5(resume_hash | jd_id)，命中即直接返回，避免重复调用 LLM
CREATE TABLE IF NOT EXISTS `analysis_cache` (
  `id`                INT          NOT NULL AUTO_INCREMENT,
  `cache_key`         VARCHAR(64)  NOT NULL COMMENT '缓存键 md5(resume_hash|jd_id)',
  `resume_hash`       CHAR(32)     NOT NULL COMMENT '简历文本 md5',
  `jd_id`             INT          NULL     COMMENT '命中的 JD id（用户直传 JD 文本时为空）',
  `position_category` VARCHAR(64)  NULL,
  `experience_level`  VARCHAR(32)  NULL,
  `score`             INT          NULL COMMENT '匹配度得分 1-100',
  `analysis`          TEXT         NULL COMMENT '匹配度分析报告',
  `rewrite`           TEXT         NULL COMMENT '改写建议',
  `hit_count`         INT          NOT NULL DEFAULT 0 COMMENT '累计命中次数',
  `created_at`        DATETIME     DEFAULT CURRENT_TIMESTAMP,
  `last_hit_at`       DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_cache_key` (`cache_key`),
  INDEX `idx_jd` (`jd_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分析结果缓存';

-- ---------- 3. 匹配日志 ----------
CREATE TABLE IF NOT EXISTS `match_log` (
  `id`                INT          NOT NULL AUTO_INCREMENT,
  `resume_hash`       CHAR(32)     NOT NULL,
  `position_category` VARCHAR(64)  NULL,
  `experience_level`  VARCHAR(32)  NULL,
  `matched_jd_id`     INT          NULL,
  `matched_title`     VARCHAR(255) NULL,
  `similarity`        FLOAT        NULL COMMENT '相似度 0-1',
  `created_at`        DATETIME     DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  INDEX `idx_resume` (`resume_hash`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='简历->岗位匹配日志';

-- ---------- 4. 缓存命中统计 ----------
-- 单行累计命中/未命中，跨进程重启也可累加
CREATE TABLE IF NOT EXISTS `cache_stats` (
  `id`     TINYINT NOT NULL DEFAULT 1,
  `hits`   BIGINT  NOT NULL DEFAULT 0 COMMENT '缓存命中次数',
  `misses` BIGINT  NOT NULL DEFAULT 0 COMMENT '缓存未命中次数',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='缓存命中统计';

INSERT IGNORE INTO `cache_stats` (`id`, `hits`, `misses`) VALUES (1, 0, 0);

-- ============================================================
-- 示例 JD 种子数据（可按需增删）
-- ============================================================
INSERT INTO `jds` (`position_category`, `experience_level`, `title`, `content`, `skills`) VALUES
(
  '后端',
  '中级',
  'Python 后端开发工程师（中级）',
  '岗位职责：负责核心业务系统的后端服务设计与开发，使用 Python（FastAPI/Django）构建高并发 API，基于 MySQL/Redis 做数据存储与缓存，熟悉消息队列与微服务架构。\n任职要求：1. 本科及以上学历，3 年以上后端开发经验；2. 精通 Python，熟悉 FastAPI/Flask/Django 至少一个框架；3. 熟练使用 MySQL，具备索引优化与慢查询调优能力；4. 熟悉 Redis、RabbitMQ/Kafka；5. 有容器化（Docker/K8s）部署经验者优先。',
  '["Python","FastAPI","MySQL","Redis","Docker","微服务","消息队列"]'
),
(
  '后端',
  '高级',
  '资深 Golang 后端架构师',
  '岗位职责：主导高并发分布式系统的架构设计，使用 Golang 构建云原生服务，负责稳定性、性能与可观测性体系建设。\n任职要求：1. 5 年以上后端经验，3 年以上 Golang 生产经验；2. 深入理解分布式系统、一致性协议、服务治理；3. 精通 MySQL/ TiDB、Redis、Kafka；4. 有大规模高并发系统从 0 到 1 经验；5. 具备团队技术规划能力。',
  '["Golang","分布式","MySQL","TiDB","Kafka","云原生","高并发"]'
),
(
  '前端',
  '初级',
  '前端开发工程师（初级）',
  '岗位职责：参与 Web 前端页面开发与维护，基于 React/Vue 实现交互效果。\n任职要求：1. 计算机相关专业，1 年以上前端经验或优秀应届生；2. 扎实的 HTML/CSS/JavaScript 基础；3. 熟悉 React 或 Vue 至少一个框架；4. 了解 TypeScript、Webpack；5. 良好的学习与沟通能力。',
  '["JavaScript","React","Vue","TypeScript","HTML","CSS"]'
),
(
  '前端',
  '中级',
  '高级前端工程师（React）',
  '岗位职责：负责中后台与 C 端复杂前端应用架构，主导组件库与工程化建设。\n任职要求：1. 3 年以上前端经验；2. 精通 React 及其生态（Redux/Zustand、Next.js）；3. 深入 TypeScript、Webpack/Vite 工程化；4. 熟悉前端性能优化与监控；5. 有 Node.js BFF 经验者优先。',
  '["React","TypeScript","Next.js","Vite","性能优化","Node.js"]'
),
(
  '算法',
  '高级',
  '机器学习算法专家',
  '岗位职责：负责推荐/搜索/NLP 方向的算法研发与落地，从特征工程到模型训练部署全链路。\n任职要求：1. 硕士及以上，3 年以上算法经验；2. 精通 Python，熟悉 PyTorch/TensorFlow；3. 深入理解推荐系统、深度学习、NLP；4. 有大规模数据建模与线上 AB 实验经验；5. 顶会论文或大模型落地经验优先。',
  '["Python","PyTorch","深度学习","NLP","推荐系统","大模型"]'
),
(
  '数据',
  '中级',
  '数据分析师（中级）',
  '岗位职责：负责业务数据分析、指标体系搭建与可视化看板，输出决策支持报告。\n任职要求：1. 3 年以上数据分析经验；2. 精通 SQL，熟悉 Hive/Spark；3. 熟练使用 Python（pandas）做数据处理；4. 熟悉 Tableau/BI 工具；5. 具备业务敏感度与数据叙事能力。',
  '["SQL","Python","pandas","Hive","BI","数据可视化"]'
),
(
  '测试',
  '中级',
  '自动化测试工程师',
  '岗位职责：负责服务端/前端自动化测试体系建设，编写接口与 UI 自动化用例。\n任职要求：1. 3 年以上测试经验；2. 熟悉 Python/Java 任一，会 pytest/Selenium；3. 熟悉接口测试、性能测试（JMeter）；4. 了解 CI/CD 与 Docker；5. 有质量保障体系建设经验优先。',
  '["Python","pytest","Selenium","JMeter","接口测试","CI/CD"]'
),
(
  '产品',
  '高级',
  '高级产品经理（B 端）',
  '岗位职责：负责 B 端 SaaS 产品规划与全生命周期管理，对接客户需求与研发落地。\n任职要求：1. 5 年以上 B 端产品经验；2. 具备出色的需求洞察与原型设计能力；3. 熟悉敏捷协作与数据驱动决策；4. 良好的跨团队沟通与项目推进能力；5. 有商业化经验优先。',
  '["需求分析","原型设计","SaaS","敏捷","数据驱动","项目管理"]'
);
