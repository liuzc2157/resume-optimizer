# Resume Optimizer Agent —— 部署与运行说明

后端：FastAPI + LangGraph + MySQL/SQLite + 两级缓存 + 按岗位/经验智能匹配。
前端：Next.js（智能匹配 / 岗位库 / 缓存命中看板）。

> **推荐路线：先本地跑通**（零密码、零 Docker、零 API Key），再按需上云（Railway / CloudBase）。

---

## 一、本地优先（推荐先跑通）

### 模式 A：SQLite 零依赖（免装数据库、免密码、免 API Key）

```bash
cd resume-optimizer-backend
python -m venv .venv && .venv\Scripts\activate        # 或你已有的虚拟环境
pip install -r requirements.txt

# 一键端到端验证（建表+种子+智能匹配+缓存命中），无需任何密钥
DB_TYPE=sqlite DB_SQLITE_PATH=./dev.db MOCK_LLM=1 \
  python scripts/live_smoke.py
```

验证通过后，直接起服务：

```bash
DB_TYPE=sqlite DB_SQLITE_PATH=./dev.db MOCK_LLM=1 \
  uvicorn app.main:app --reload --port 8000
# 打开 http://localhost:8000/docs
```

- `MOCK_LLM=1`：LLM 返回固定模拟文本（含可解析的 `SCORE: 88`），方便离线验证缓存命中。
- 想接真实 LLM：去掉 `MOCK_LLM`，设 `GLM_API_KEY`（或 `DEEPSEEK_API_KEY` + `LLM_PROVIDER=deepseek`）。

### 模式 B：连你本机 MySQL（真实 MySQL 链路）

```bash
cp .env.example .env
# 编辑 .env：DB_TYPE=mysql，并填好 DB_PASSWORD（其余默认指向 127.0.0.1:3306）

# 一键建库 + 建表 + 写入示例 JD + 初始化命中统计（种子已内置，无需手动跑 init.sql）
python scripts/setup_local.py

# 起服务（接真实 LLM 需填 GLM_API_KEY）
uvicorn app.main:app --reload --port 8000
```

> MySQL 模式同样支持 `MOCK_LLM=1` 先验证全链路；验证完再填真实 Key。

---

## 二、部署到云端（本地跑通后再做）

### 选项 1：Railway（整套一键，含 MySQL）—— 最省心

仓库已自带 `railway.json`。步骤：

```bash
npm i -g @railway/cli && railway login
railway link                       # 关联到你的 Railway 项目
railway add                       # 添加 MySQL 插件（自动注入 DATABASE_URL 等）
# 在 Railway 控制台「Variables」设置：
#   DB_TYPE=mysql，并把 MySQL 插件给出的 HOST/PORT/USER/PASSWORD/NAME 映射到 DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME
#   GLM_API_KEY=你的Key；MOCK_LLM=0
railway up
```

- Railway 的 MySQL 插件会提供内网连接信息，直接映射到 `DB_*` 即可，无需公网暴露。
- 前端：`frontend` 用 `next build`（已配 `output:"export"`）产出 `out/`，可单独在 Railway 起一个 Static 服务，或部署到 Vercel。

### 选项 2：CloudBase（你最初选定的目标）

前置：`npm i -g @cloudbase/cli` → `tcb login`；控制台开通云数据库 MySQL（个人版已自带 MySQL，无需另购）。

```bash
# 控制台「环境变量」注入：DB_TYPE=mysql、DB_HOST/DB_USER/DB_PASSWORD/DB_NAME（云端 MySQL 内网地址）、
# GLM_API_KEY、BACKEND_CORS_ORIGINS（前端域名）、NEXT_PUBLIC_API_URL（后端域名，构建期写死需提前 export）
export NEXT_PUBLIC_API_URL="https://<后端容器域名>"
tcb framework deploy
```

> ⚠️ 云端函数/容器跑在腾讯云内网，连不上你笔记本的 `localhost:3306`。无论选 CloudBase 还是 Railway，
> MySQL 必须部署在**与后端同一可达网络**的实例（云端 MySQL / Railway MySQL 插件）。

---

## 三、环境变量清单

| 变量 | 说明 | 默认 |
|---|---|---|
| DB_TYPE | `mysql`（默认） / `sqlite`（本地零依赖） | mysql |
| DB_HOST | MySQL 地址 | 127.0.0.1 |
| DB_PORT | MySQL 端口 | 3306 |
| DB_USER | MySQL 用户 | root |
| DB_PASSWORD | MySQL 密码（mysql 模式必填） | — |
| DB_NAME | 库名 | resume_optimizer |
| DB_SQLITE_PATH | sqlite 模式文件路径 | ./resume_optimizer.db |
| MOCK_LLM | `1` 时 LLM 返回模拟文本，免 API Key | 0 |
| LLM_PROVIDER | `glm`（默认）/ `deepseek` | glm |
| GLM_API_KEY / DEEPSEEK_API_KEY | 真实 LLM Key | — |
| BACKEND_CORS_ORIGINS | 前端域名（逗号分隔） | 本地 |

---

## 四、核心接口

- `POST /optimize` 任务型：匹配岗位 → 查缓存 →（未命中）LLM 分析改写，返回 `cache_hit`
- `POST /chat` 对话型：多轮 Agent，支持按岗位/经验智能匹配与缓存
- `POST /match` 智能匹配：按岗位类别+经验等级返回候选岗位 Top-N
- `GET /jds` `POST /jds` `DELETE /jds/{id}` 岗位库管理
- `GET /cache/stats` 缓存命中统计（hits / misses / 命中率）
- `GET /health` 健康检查 + DB 探活

---

## 五、缓存与匹配说明

- **缓存键** = `md5(版本 | 归一化简历 | 岗位引用)`，岗位引用可以是库内 `jd:<id>` 或用户直传 JD 的 `text:<md5>`。
- **三级缓存**：进程内 LRU → 数据库精确键 → **语义近似（同岗位分区内，余弦相似度 ≥ 0.95）** → 调用 LLM。
  语义层是精确缓存覆盖不到的场景（简历改了几个字、调了语序）的关键补充；
  为防误命中，检索**只在同一个岗位分区内**进行，且阈值取保守的 0.95。
- **智能匹配**：jieba 分词（缺失退化为中文 bigram）+ 技能加权重叠系数，先按「岗位类别 + 经验等级」过滤候选再排序；JD 解析优先级：直传 `jd_text` > 指定 `jd_id` > 智能匹配。

## 六、要不要上 Redis？

**结论：现阶段不需要，等到多实例部署时再加。**

当前缓存层次已经能自洽工作：

| 层 | 位置 | 是否跨进程共享 | 作用 |
|---|---|---|---|
| L1 精确 | 进程内 LRU | ❌ 仅本进程 | 热路径零开销 |
| L2 精确 | MySQL `analysis_cache` | ✅ | 重启/多实例都能命中 |
| L3 语义 | MySQL（分区内向量比对） | ✅ | 抓「改了几个字」的近似重复 |

**什么情况下才真正需要 Redis：**

1. **后端跑了多个实例**（云端自动扩缩容、Railway/CloudBase 多副本）——
   此时每个实例的 L1 各自为政，Redis 可以作为**共享的 L1.5**，显著减少对 MySQL 的查询。
2. **QPS 很高**，MySQL 每次请求一次查询成为瓶颈——Redis 读写比 MySQL 快一个数量级。
3. **想要原生 TTL 自动过期**——目前 MySQL 缓存靠 `CACHE_VERSION` 整体失效，不过期；
   若希望「7 天未命中的缓存自动清理」，Redis 的 TTL 更省事。
4. **语义检索要上规模**——现在每个分区最多扫 200 条做余弦比对（`SEMANTIC_CACHE_SCAN`），
   数据量大后应换 Redis 的向量检索（RediSearch）或专用向量库（FAISS/Milvus）。

**代价**：Redis 是内存型，重启即丢（不过 MySQL 才是真相源，丢了只是回到 L2/L3，不会出错）；
另外要多运维一个服务，本地 Windows 还得靠 WSL/Docker 跑。

**怎么加（未来需要时）**：在 `app/cache.py` 的 `get_or_compute` 里，
于「内存 LRU」和「MySQL 精确键」之间插一层 Redis 查询即可，
业务代码与接口签名都不用动——这也是当初把缓存收敛到单一模块的原因。

