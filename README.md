# 简遇 Briefly · AI 简历优化

基于 **FastAPI + LangGraph + MySQL/SQLite** 的简历匹配分析 & 对话式改写平台，配套 **Next.js 14** 前端。
支持任务型一键优化、多轮对话改写、RAG 语义匹配、三级缓存、账号配额与隐私合规（可注销删数据）。

## 功能特性

- **任务型优化**（`POST /optimize`）：简历 + JD → ATS 多维评分（关键词/经验/量化/动词/结构）+ AI 分析 + 改写建议
- **对话式改写**（`POST /chat`）：LangGraph 多轮对话，Checkpointer **持久化到数据库**（服务重启可续聊）
- **智能匹配**（`POST /match`、`/rag/retrieve`）：无明确 JD 时按「岗位类别 + 经验等级」或语义向量召回最匹配岗位
- **三级缓存**：内存 LRU → DB 精确键 → 语义近似命中，命中即零 LLM 成本
- **账号体系**：邮箱验证码注册（一个邮箱一个账号）、用户名或邮箱登录、忘记密码（邮箱验证码重置）、每日配额、请求限流、注销即彻底删除个人数据
- **可观测性**：结构化日志（`LOG_FORMAT=json`）、每次调用写入 `usage_log`（token 用量 + 缓存命中）

## 目录结构

```
resume-optimizer-backend/
├── app/
│   ├── main.py          # FastAPI 入口：路由 / 鉴权依赖 / 结构化日志
│   ├── auth.py          # 注册 / 登录 / 个人信息 / 注销（含数据清除）
│   ├── security.py      # PBKDF2 密码哈希 + JWT 签发校验
│   ├── rate_limit.py    # 每用户限流 + 每日配额（免费档默认 10 次/天）
│   ├── usage.py         # 用量统计（usage_log）
│   ├── checkpointer.py  # LangGraph 持久化 Checkpointer（SQLAlchemy）
│   ├── models.py        # SQLAlchemy 模型（users / usage_log / chat_* / jds / analysis_cache）
│   ├── db.py            # 连接 / 建表 / 老库兜底加列
│   ├── graph.py         # 任务型 LangGraph
│   ├── chat_graph.py    # 对话型 LangGraph
│   ├── nodes.py         # 核心节点（分析 / 改写）
│   ├── llm.py           # LLM 供应商适配 + token 用量统计
│   ├── cache.py / rag.py / matching.py / scoring.py / embedding.py / file_parser.py
│   └── seed_data.py     # 种子岗位
├── tests/               # pytest（28 个用例）
├── frontend/            # Next.js 14 前端（登录 / 三模式 / 配额展示）
├── .github/workflows/ci.yml
├── requirements.txt / requirements-dev.txt
└── .env.example
```

## 快速开始（后端）

```bash
cd resume-optimizer-backend
python -m venv .venv
# Windows: .\.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

# 复制并编辑配置（至少填 JWT_SECRET；免费跑通可 DB_TYPE=sqlite + MOCK_LLM=1）
cp .env.example .env

# 启动（默认 8000）
uvicorn app.main:app --reload --port 8000
```

开发期最快验证链路（无需 MySQL / 真实 LLM）：

```env
DB_TYPE=sqlite
MOCK_LLM=1
JWT_SECRET=随便一个随机长字符串
```

## 前端

```bash
cd frontend
npm install
# 后端地址默认 http://localhost:8000，可配 NEXT_PUBLIC_API_URL 覆盖
npm run dev        # http://localhost:3000
```

前端会先进入登录/注册页；登录后展示剩余配额，可随时「注销并删除数据」。

## API 一览

| 方法 | 路径 | 说明 | 鉴权 |
| --- | --- | --- | --- |
| POST | `/auth/send-code` | 发送邮箱验证码（注册/找回共用） | 公开（IP 限流） |
| POST | `/auth/register` | 注册（邮箱+验证码，需 `consent=true`） | 公开（IP 限流） |
| POST | `/auth/login` | 登录（用户名或邮箱），返回 JWT | 公开（IP 限流） |
| POST | `/auth/forgot-password` | 忘记密码：向注册邮箱发重置码 | 公开（IP 限流） |
| POST | `/auth/reset-password` | 用验证码重置密码并返回新令牌 | 公开（IP 限流） |
| GET | `/users/me` | 个人信息 + 配额 + 用量 | Bearer |
| DELETE | `/users/me` | 注销并清除全部个人数据 | Bearer |
| POST | `/optimize` | 一键优化（计入配额） | Bearer |
| POST | `/chat` | 对话（计入配额，DB 持久化） | Bearer |
| POST | `/match` / `/rag/retrieve` | 智能匹配 | Bearer |
| POST | `/compare` | 多岗位横向对比（计入配额） | Bearer |
| GET/POST/PUT/DELETE | `/jds*` | 岗位库管理（种子全局 + 本人私有） | Bearer |
| GET | `/cache/stats` | 缓存命中统计 | Bearer |
| POST | `/vector/reindex` | 重建向量索引 | 仅管理员 |
| GET | `/health` | 健康检查 | 公开 |

调用示例：

```bash
# 登录拿 token
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"account":"demo","password":"secret123"}'

# 带 token 调用业务接口
curl -s -X POST http://localhost:8000/optimize \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"resume_text":"...","jd_text":"..."}'
```

## 账号与邮箱验证

- **注册**：`email` + 6 位验证码 + `consent=true`，一个邮箱只能注册一个账号（重复注册返回 409）；`username` 仍全局唯一。
- **验证码流程**：先 `POST /auth/send-code`（`purpose=register|reset`），验证码 10 分钟内有效、一次性、错误 5 次作废；同一邮箱重发有 60s 冷却。
- **SMTP 配置**：配置 `SMTP_HOST/PORT/SECURITY/USER/PASSWORD/FROM` 后验证码走真实邮件；未配置时自动进入**开发模式**，接口响应直接返回 `dev_code`（前端自动填入），方便本地联调。
- **忘记密码**：登录页「忘记密码？」→ 邮箱收码 → `POST /auth/reset-password` 重置并直接登录。
- **登录**：`/auth/login` 传 `account`（用户名 **或** 邮箱）+ 密码。

## 向量 / 语义检索说明

- 项目已内置**向量化能力**：简历/JD 文本向量化后存于数据库（MySQL/SQLite 的 `embedding` 字段），语义缓存与 `/match?use_rag=true`、`/rag/retrieve` 用余弦相似度做召回，命中即复用上次分析（对应「相似问题快速复用」）。
- 默认用本地哈希向量（零依赖、离线可用、跨进程稳定）；配置 `EMBEDDING_PROVIDER=api` + 密钥可换真实语义向量（如智谱 `embedding-3` / 硅基流动 `bge-m3`），**接口不可用或限流时自动回退本地向量**，检索链路不中断。
- 岗位库当前约 33 条，库内向量 + Python 余弦完全够用；数据量上到十万级再换专用 ANN 向量库（`faiss-cpu` / `pgvector` / Milvus / Pinecone，见 `requirements.txt` 注释）。

## 配额 / 限流 / 隐私

- **每日配额**：免费档默认 `FREE_DAILY_QUOTA=10` 次/天，用完返回 `429`；`/users/me` 可查剩余。
  计入配额的接口：`/optimize`、`/chat`、`/compare`、`/match`（仅 `use_rag=true` 时）、`/rag/retrieve`。
  判定口径是「是否真的会调 LLM」：`/match?use_rag=false` 只做关键词重叠 + 技能加权的本地计算，
  以 `/match/keyword` 单独记账，**不占配额**。
- **请求限流**：每用户每分钟 `RATE_LIMIT_PER_MINUTE`（默认 30）滑动窗口；`/auth` 按 IP 限流防爆破。
- **隐私合规**：注册必须同意《隐私政策》；所有业务数据按 `user_id` 隔离（缓存/JD/会话互不可见）；注销会删除该用户全部数据（含向量）。

## 测试与 CI

```bash
python -m pytest tests -q     # 40 个用例（SQLite + MOCK_LLM，含验证码/找回密码）
```

GitHub Actions（`.github/workflows/ci.yml`）在 push / PR 时自动跑后端 pytest 与前端 `tsc --noEmit`。

## 生产部署注意

- 必须显式设置 `JWT_SECRET`（`python -c "import secrets; print(secrets.token_urlsafe(48))"`），否则多实例无法互认令牌、重启即掉登录。
- 建议 `DB_TYPE=mysql`（见 `railway.json` / `Dockerfile` 部署参考），老库会自动补列（`ensure_columns`）。
- `BACKEND_CORS_ORIGINS` 填真实前端域名。
- 免费档配额与费用有关，上线前按成本核算 `FREE_DAILY_QUOTA` 与 `LLM_MODEL` 单价。