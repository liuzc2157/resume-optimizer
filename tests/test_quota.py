"""配额与限流测试。"""

from conftest import make_user
from sqlalchemy import update

from app.db import get_session
from app.models import User


def test_quota_exhaustion_returns_429(client):
    u = make_user(client, f"quota{__import__('uuid').uuid4().hex[:6]}")
    # 把该用户配额改成 2（覆盖环境默认 50）
    with get_session() as s:
        s.execute(update(User).where(User.id == u["id"]).values(daily_quota=2))
        s.commit()

    for i in range(2):
        r = client.post(
            "/optimize",
            json={
                "resume_text": f"Python 后端简历 {i}",
                "jd_text": "招聘 Python 后端，要求 Python/FastAPI",
            },
            headers=u["headers"],
        )
        assert r.status_code == 200, r.text
    # 第 3 次应触发 429
    r = client.post(
        "/optimize",
        json={"resume_text": "再试一次", "jd_text": "招聘 Python 后端"},
        headers=u["headers"],
    )
    assert r.status_code == 429, r.text


def test_usage_recorded(client, new_user):
    r = client.post(
        "/optimize",
        json={
            "resume_text": "Python 后端 3 年经验",
            "jd_text": "招聘 Python 后端，要求 Python/FastAPI/MySQL",
        },
        headers=new_user["headers"],
    )
    assert r.status_code == 200
    me = client.get("/users/me", headers=new_user["headers"]).json()
    assert me["usage"]["total_calls"] >= 1
    assert me["quota"]["unlimited"] is False


def test_free_quota_reflected_in_me(client, new_user):
    me = client.get("/users/me", headers=new_user["headers"]).json()
    assert me["quota"]["daily"] == 50  # conftest 里的 FREE_DAILY_QUOTA
    assert me["quota"]["remaining"] == 50
