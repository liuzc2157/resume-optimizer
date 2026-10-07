"""本地端到端冒烟测试（真实 FastAPI 应用 + 真实缓存层 + 真实 SQLite）。

用途：无需 API Key、无需 Docker、无需本地 MySQL 密码，即可验证：
  - 应用能启动、建表、写入示例 JD
  - 智能匹配按「岗位类别 + 经验等级」返回排序候选
  - 同一份简历+岗位，第二次请求命中缓存（cache_hit=True，compute 只跑一次）
  - /cache/stats 命中率正确累加

运行：python scripts/live_smoke.py
"""

import os
import sys
import json
import tempfile

sys.path.insert(0, os.getcwd())

# 必须在导入 app 之前设置好环境变量
_db_path = os.path.join(tempfile.gettempdir(), "ropt_live_smoke.db")
if os.path.exists(_db_path):
    os.remove(_db_path)
os.environ.update(
    {
        "DB_TYPE": "sqlite",
        "DB_SQLITE_PATH": _db_path,
        "MOCK_LLM": "1",
        "BACKEND_CORS_ORIGINS": "http://localhost:3000",
    }
)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

RESUME = (
    "熟练掌握 Python，使用 FastAPI 构建后端 API，熟悉 MySQL 与 Redis，"
    "使用 Docker 容器化部署，了解微服务与消息队列。"
)

ok = True


def show(label: str, r) -> None:
    print(f"\n=== {label} -> HTTP {r.status_code} ===")
    try:
        print(json.dumps(r.json(), ensure_ascii=False, indent=2)[:1200])
    except Exception:
        print(r.text[:400])


def check(cond: bool, msg: str) -> None:
    global ok
    if not cond:
        ok = False
        print("  ✗ FAIL:", msg)
    else:
        print("  ✓", msg)


def run(client: TestClient) -> None:
    # 1) 健康检查
    r = client.get("/health")
    show("GET /health", r)
    check(r.status_code == 200 and r.json().get("status") == "ok", "/health 正常且 db 已连接")

    # 2) 岗位库（应已自动写入示例 JD；数量会随种子扩充，故断言下界而非固定值）
    r = client.get("/jds")
    show("GET /jds", r)
    jds = r.json()
    check(
        r.status_code == 200 and len(jds) >= 27,
        f"/jds 返回 {len(jds)} 条示例 JD（期望 >= 27）",
    )

    # 3) 智能匹配：后端 + 中级
    r = client.post(
        "/match",
        json={"resume_text": RESUME, "position_category": "后端", "experience_level": "中级", "top_n": 3},
    )
    show("POST /match (后端/中级)", r)
    matches = r.json().get("matches", [])
    check(r.status_code == 200 and len(matches) >= 1, "/match 返回候选")
    check(matches and matches[0]["position_category"] == "后端", "Top1 命中岗位类别=后端")

    # 4) 任务型优化：第一次（应未命中缓存）
    r1 = client.post(
        "/optimize",
        json={"resume_text": RESUME, "position_category": "后端", "experience_level": "中级"},
    )
    show("POST /optimize #1", r1)
    j1 = r1.json()
    check(r1.status_code == 200, "/optimize #1 返回 200")
    check(j1.get("cache_hit") is False, "#1 cache_hit=False（未命中）")
    # 注意：得分已改为「多维度规则加权」的确定性分数（不再是 LLM/mock 的固定 88），
    # 这样离线也能得到真实、可解释的分数。
    score1 = j1.get("score")
    bd = j1.get("breakdown") or {}
    check(
        isinstance(score1, int) and 0 <= score1 <= 100,
        f"#1 得分在 0-100 区间（实际 {score1}）",
    )
    check(
        bd.get("total") == score1,
        f"#1 得分与 breakdown.total 一致（{score1} vs {bd.get('total')}）",
    )
    check(
        len(bd.get("dimensions", [])) == 5,
        f"#1 含 5 个评分维度（实际 {len(bd.get('dimensions', []))}）",
    )

    # 5) 任务型优化：第二次（相同输入，应命中缓存）
    r2 = client.post(
        "/optimize",
        json={"resume_text": RESUME, "position_category": "后端", "experience_level": "中级"},
    )
    show("POST /optimize #2 (应命中缓存)", r2)
    j2 = r2.json()
    check(r2.status_code == 200, "/optimize #2 返回 200")
    check(j2.get("cache_hit") is True, "#2 cache_hit=True（命中缓存）")
    check(j2.get("matched_jd") is not None, "#2 带匹配到的岗位信息")

    # 5.5) 语义近似命中：简历只改了几个字 → 精确键不同，但应在「同岗位分区内」语义命中。
    #      这是精确缓存（逐字比对）做不到、而语义缓存能覆盖的场景。
    r3 = client.post(
        "/optimize",
        json={
            "resume_text": RESUME + "，沟通能力良好。",
            "position_category": "后端",
            "experience_level": "中级",
        },
    )
    show("POST /optimize #3 (轻微改写，应语义命中)", r3)
    j3 = r3.json()
    check(r3.status_code == 200, "/optimize #3 返回 200")
    check(
        j3.get("cache_source") == "semantic",
        f"#3 语义命中（实际 source={j3.get('cache_source')}，相似度={j3.get('cache_similarity')}）",
    )

    # 6) 缓存命中统计
    r = client.get("/cache/stats")
    show("GET /cache/stats", r)
    cs = r.json()
    check(r.status_code == 200 and cs.get("hits", 0) >= 1, f"/cache/stats hits>={cs.get('hits',0)}>=1")
    check(cs.get("misses", 0) >= 1, "misses>=1（首次未命中已计入）")
    check(cs.get("semantic_hits", 0) >= 1, f"semantic_hits>=1（实际 {cs.get('semantic_hits')}）")


if __name__ == "__main__":
    # with 块会触发 lifespan/startup，从而建表并写入示例 JD
    with TestClient(app) as client:
        run(client)
    print("\n" + ("✅ 全部通过：本地端到端跑通（SQLite + 模拟LLM）" if ok else "❌ 存在失败项，请检查上方日志"))
    sys.exit(0 if ok else 1)
