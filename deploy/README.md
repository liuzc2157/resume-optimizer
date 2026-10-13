# Resume Optimizer Agent —— 部署与运行说明

后端：FastAPI + LangGraph + **Postgres / SQLite** + 三级缓存 + 按岗位/经验智能匹配。
前端：Next.js（快速优化 / 对话 / 智能匹配 / 岗位库 / 缓存看板）。

> 数据库策略：**本地开发用 SQLite（零依赖）**，**云端用 Postgres**（Railway Postgres 或 Supabase 二选一）。MySQL 已移除。

---

## 一、本地开发（推荐先跑通）

SQLite 零依赖，免装数据库、免密码：

```bash
cd resume-optimizer-backend
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt

# 后端（默认读 .env，DB_TYPE=sqlite，数据在 ./resume_optimizer.db）
uvicorn app.main:app --reload --port 8001

# 前端（另开终端）
cd frontend
npm install
NEXT_PUBLIC_API_URL=http://localhost:8001 npm run dev
# 打开 http://localhost:3000
```

- 需要真实 AI：在 `.env` 填 `GLM_API_KEY`（当前已填），`MOCK_LLM=0`。
- 初始化脚本（建表 + 种子岗位，幂等）：`python scripts/setup_local.py`

---

## 二、部署到 Railway（推荐）

仓库已自带 `railway.json`（两个服务：`api` 后端 + `web` 前端）与两套 Dockerfile，Docker 自动构建。

### 方式 A：Postgres（推荐，数据持久且多实例可共享）

1. **推送代码到 GitHub**，在 Railway 新建项目 → Deploy from GitHub repo；或本地用 CLI：
   ```bash
   npm i -g @railway/cli && railway login
   railway link
   railway up
   ```
2. **创建 Postgres 数据库**：Railway 控制台 → 项目 → `New` → `Database` → 选 PostgreSQL。创建后把它 **Link 到 `api` 服务**，Railway 会自动注入 `DATABASE_URL`。
3. **给 `api` 服务设置环境变量**：
   ```
   DB_TYPE=postgres
   LLM_PROVIDER=glm
   GLM_API_KEY=<你的智谱 Key>
   MOCK_LLM=0
   # DATABASE_URL 由 Postgres 插件自动注入，无需手填
   ```
4. **给 `web` 前端设置构建变量**（`NEXT_PUBLIC_API_URL` 是构建期变量）：
   ```
   NEXT_PUBLIC_API_URL=https://<api 服务的 Railway 域名>
   ```
5. 等部署完成，打开 `web` 服务的公网域名即可使用。

> `api` 服务上的 `/data` Volume 可保留（SQLite 兜底用），用 Postgres 时不会用到。

### 方式 B：SQLite + Volume（轻量，不建数据库）

保持 `DB_TYPE=sqlite`，把 `api` 服务的 Volume 挂到 `/data`，SQLite 文件持久化在 `/data/resume_optimizer.db`（容器内已默认这个路径）。重启数据不丢，但不支持多实例并发。

---

## 三、改用 Supabase（Postgres，等价于方式 A 的数据库替换）

不想在 Railway 建数据库时，用 Supabase 的 Postgres 即可：

1. Supabase 控制台建项目 → 左侧 `Connect` → 复制 **Session pooler（端口 5432）** 或 **Direct connection** 的连接串。
2. 把它填到 `api` 服务环境变量：
   ```
   DB_TYPE=postgres
   DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
   GLM_API_KEY=<你的智谱 Key>
   MOCK_LLM=0
   ```
3. 首次启动会自动建表 + 写入种子岗位（幂等）。若连接串带 `sslmode` 等参数，SQLAlchemy 直接透传即可。

> 与 Railway Postgres 完全同一套代码路径，仅 `DATABASE_URL` 不同。

---

## 四、环境变量一览

| 变量 | 说明 | 本地默认 |
| --- | --- | --- |
| `DB_TYPE` | `sqlite`（本地） / `postgres`（云端） | sqlite |
| `DATABASE_URL` | Postgres 连接串（postgres 模式必填；Railway/Supabase 都直接给） | — |
| `DB_SQLITE_PATH` | SQLite 文件路径 | ./resume_optimizer.db |
| `LLM_PROVIDER` | glm / deepseek / … | glm |
| `GLM_API_KEY` | 智谱 Key（glm-4-flash 免费） | — |
| `MOCK_LLM` | `1` 走模拟文本离线验证 | 0 |
| `NEXT_PUBLIC_API_URL` | 前端调后端地址（构建期） | http://localhost:8001 |

---

## 五、验证部署

- 后端健康检查：`GET https://<api域名>/health` 应返回 `{"status":"ok"}` 且含数据库信息。
- 前端打开后能注册/登录、保存简历、跑快速优化/对话/智能匹配，说明前后端 + 数据库全链路正常。
- 云端表结构由启动时的 `init_db()` 自动创建，无需手工跑 SQL。
