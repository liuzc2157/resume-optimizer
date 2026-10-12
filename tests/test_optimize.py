"""任务型优化 + 缓存命中 + 用户隔离测试。"""

from conftest import register_user

RESUME = "Python 后端 3 年经验 FastAPI MySQL Redis 微服务 高并发"
JD = "招聘 Python 后端，要求 Python/FastAPI/MySQL"


def _optimize(client, headers, resume=RESUME, jd=JD):
    return client.post(
        "/optimize", json={"resume_text": resume, "jd_text": jd}, headers=headers
    )


def test_optimize_returns_full_result(client, new_user):
    r = _optimize(client, new_user["headers"])
    assert r.status_code == 200, r.text
    data = r.json()
    assert 1 <= data["score"] <= 100
    assert data["analysis"]
    assert data["rewrite"]
    assert data["cache_hit"] is False


def test_cache_hit_on_second_call(client, new_user):
    r1 = _optimize(client, new_user["headers"])
    r2 = _optimize(client, new_user["headers"])
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["cache_hit"] is False
    assert r2.json()["cache_hit"] is True


def test_cache_isolated_between_users(client, new_user):
    """同一份简历+JD，不同用户之间缓存必须隔离（隐私）。"""
    r1 = _optimize(client, new_user["headers"])
    assert r1.json()["cache_hit"] is False

    other_headers = register_user(client)["headers"]
    r2 = _optimize(client, other_headers)
    assert r2.status_code == 200
    assert r2.json()["cache_hit"] is False  # 不应命中他人缓存


def test_match_endpoint(client, new_user):
    r = client.post(
        "/match",
        json={"resume_text": RESUME, "top_n": 3},
        headers=new_user["headers"],
    )
    assert r.status_code == 200
    assert isinstance(r.json()["matches"], list)


def test_classify_endpoint(client, new_user):
    r = client.post(
        "/classify", json={"resume_text": "Python 后端 FastAPI"}, headers=new_user["headers"]
    )
    assert r.status_code == 200
    assert r.json()["category"]


def test_compare_endpoint(client, new_user):
    r = client.post(
        "/compare",
        json={"resume_text": RESUME, "jd_ids": [1, 2]},
        headers=new_user["headers"],
    )
    assert r.status_code == 200
    assert "items" in r.json()
