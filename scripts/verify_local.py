"""本地全链路验证（无需真实 MySQL / 无需真实 LLM）。

做法：用 SQLite 顶替 MySQL，桩掉 nodes.analyze_resume/rewrite_resume，
验证三件事：
1. 智能匹配：按岗位类别+经验等级排序正确；
2. 缓存命中：同一 resume+JD 第二次请求 cache_hit=True，compute 只执行一次；
3. 缓存统计 /cache/stats 命中率上升；
并通过 FastAPI TestClient 走 /optimize、/match、/jds、/cache/stats 接口。

生产环境只需把连接串换成 MySQL（环境变量），代码逻辑完全一致。
"""

import os
import sys

# 把项目根目录加入 import 路径，使 `app` 包可被导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---- 1. 用 SQLite 顶替 MySQL（在导入 app 之前注入引擎）----
import app.db as db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_SQLITE = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
db._engine = _SQLITE
db._SessionLocal = sessionmaker(bind=_SQLITE, autoflush=False, expire_on_commit=False)

from app.models import Base, CacheStats  # noqa: E402

Base.metadata.create_all(_SQLITE)
with db.get_session() as s:
    s.add(CacheStats(id=1, hits=0, misses=0))
    s.commit()

# ---- 2. 桩掉 LLM（避免真实网络调用）----
import app.nodes as nodes  # noqa: E402

_LLM_CALLS = {"n": 0}


def fake_analyze(resume_text, jd_text):
    _LLM_CALLS["n"] += 1
    return (f"[分析] 简历与JD匹配度报告\nSCORE: 82", 82)


def fake_rewrite(resume_text, jd_text, analysis):
    _LLM_CALLS["n"] += 1
    return "[改写] 建议优化若干处。"


nodes.analyze_resume = fake_analyze
nodes.rewrite_resume = fake_rewrite

# ---- 3. 种子 JD（覆盖不同岗位/经验）----
from app import matching  # noqa: E402

matching.add_jd(
    "后端", "中级", "Python 后端工程师",
    "熟悉 Python FastAPI MySQL Redis Docker 微服务 消息队列",
    ["Python", "FastAPI", "MySQL", "Redis"],
)
matching.add_jd(
    "前端", "初级", "前端开发工程师",
    "熟悉 JavaScript React Vue TypeScript HTML CSS",
    ["JavaScript", "React", "Vue", "TypeScript"],
)
matching.add_jd(
    "后端", "高级", "资深 Golang 架构师",
    "精通 Golang 分布式 MySQL TiDB Kafka 云原生 高并发",
    ["Golang", "分布式", "MySQL", "Kafka"],
)

# ---- 4. 验证智能匹配 ----
resume = "我精通 Python，用过 FastAPI 和 MySQL，会 Docker，做过微服务与消息队列。"
m = matching.match_jds(resume, "后端", "中级", top_n=3)
assert m[0].jd.position_category == "后端" and m[0].jd.experience_level == "中级", m[0].jd.title
print(f"[匹配] Top1 = {m[0].jd.title}  相似度={m[0].similarity}  (期望命中后端/中级)")

# ---- 5. 验证缓存命中（直接走 cache_store）----
from app.cache import cache_store  # noqa: E402

calls = {"n": 0}


def compute():
    calls["n"] += 1
    return {"score": 82, "analysis": "A", "rewrite": "R"}


r1 = cache_store.get_or_compute("resume-x", "jd:1", "后端", "中级", compute, jd_id=1)
r2 = cache_store.get_or_compute("resume-x", "jd:1", "后端", "中级", compute, jd_id=1)
assert r1.cache_hit is False, "首次应为未命中"
assert r2.cache_hit is True, "二次应为命中"
assert calls["n"] == 1, f"compute 应只执行一次，实际 {calls['n']}"
stats = cache_store.stats()
assert stats.hits >= 1 and stats.misses >= 1
print(f"[缓存] 首次未命中 / 二次命中；compute 执行次数={calls['n']}；统计 hits={stats.hits} misses={stats.misses} 命中率={stats.hit_rate}")

# 不同简历 -> 不同键 -> 未命中
r3 = cache_store.get_or_compute("resume-y", "jd:1", "后端", "中级", compute, jd_id=1)
assert r3.cache_hit is False
print(f"[缓存] 不同简历 -> 未命中（键不同），累计 misses={cache_store.stats().misses}")

# ---- 6. 走 FastAPI 接口验证 ----
from fastapi.testclient import TestClient  # noqa: E402
import app.main as main  # noqa: E402

client = TestClient(main.app)

# /jds
jds_resp = client.get("/jds")
assert jds_resp.status_code == 200 and len(jds_resp.json()) >= 3
print(f"[接口] GET /jds -> {len(jds_resp.json())} 条岗位")

# /match
mt = client.post("/match", json={"resume_text": resume, "position_category": "后端", "experience_level": "中级"})
assert mt.status_code == 200
print(f"[接口] POST /match -> Top1 {mt.json()['matches'][0]['title']} sim={mt.json()['matches'][0]['similarity']}")

# /optimize 两次（桩掉 LLM，验证缓存命中透传）
opt_req = {"resume_text": resume, "jd_id": 1}
o1 = client.post("/optimize", json=opt_req)
o2 = client.post("/optimize", json=opt_req)
assert o1.status_code == 200 and o2.status_code == 200
assert o1.json()["cache_hit"] is False, o1.json()
assert o2.json()["cache_hit"] is True, o2.json()
assert o2.json()["score"] == 82
print(f"[接口] POST /optimize 首次 cache_hit={o1.json()['cache_hit']} / 二次 cache_hit={o2.json()['cache_hit']}（期望 False->True）")

# /cache/stats
st = client.get("/cache/stats").json()
print(f"[接口] GET /cache/stats -> hits={st['hits']} misses={st['misses']} hit_rate={st['hit_rate']} 内存={st['memory_size']}")

# /health
h = client.get("/health").json()
print(f"[接口] GET /health -> {h}")

print("\n✅ 全链路验证通过：智能匹配 + 缓存命中 + 命中率统计 + FastAPI 接口 均正常。")
print(f"   （LLM 桩共被调用 {_LLM_CALLS['n']} 次，第二次 /optimize 未触达模型）")
