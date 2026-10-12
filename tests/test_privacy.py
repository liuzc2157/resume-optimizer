"""隐私：注销账号应清除该用户全部个人数据（缓存/会话/用量/自建 JD）。"""

import uuid

from sqlalchemy import func, select

from conftest import register_user

from app.db import get_session
from app.models import AnalysisCache, ChatCheckpoint, JD, UsageLog, User


def test_delete_account_purges_user_data(client):
    username = f"del{uuid.uuid4().hex[:8]}"
    u = register_user(client, username=username)
    headers = u["headers"]
    uid = u["json"]["user"]["id"]

    # 制造个人数据：优化（写缓存+用量）、聊天（写会话）、上传 JD
    client.post(
        "/optimize",
        json={"resume_text": "Python 后端 3 年经验", "jd_text": "招聘 Python 后端"},
        headers=headers,
    )
    client.post("/chat", json={"message": "你好"}, headers=headers)
    jd = client.post(
        "/jds",
        json={
            "position_category": "后端",
            "experience_level": "中级",
            "title": "我的私有岗位",
            "content": "私有 JD 内容，Python 后端",
        },
        headers=headers,
    )
    assert jd.status_code == 201
    private_jd_id = jd.json()["id"]

    with get_session() as s:
        assert s.scalar(select(func.count(UsageLog.id)).where(UsageLog.user_id == uid)) > 0
        assert s.scalar(select(func.count(ChatCheckpoint.id)).where(ChatCheckpoint.user_id == uid)) > 0
        assert s.scalar(select(func.count(AnalysisCache.id)).where(AnalysisCache.user_id == uid)) > 0

    # 注销
    r = client.delete("/users/me", headers=headers)
    assert r.status_code == 204

    with get_session() as s:
        assert s.scalar(select(func.count(UsageLog.id)).where(UsageLog.user_id == uid)) == 0
        assert s.scalar(select(func.count(ChatCheckpoint.id)).where(ChatCheckpoint.user_id == uid)) == 0
        assert s.scalar(select(func.count(AnalysisCache.id)).where(AnalysisCache.user_id == uid)) == 0
        assert s.scalar(select(func.count(JD.id)).where(JD.user_id == uid)) == 0
        assert s.scalar(select(func.count(User.id)).where(User.id == uid)) == 0

    # 注销后令牌失效
    r = client.get("/users/me", headers=headers)
    assert r.status_code == 401

    # 种子岗位不受影响
    with get_session() as s:
        assert s.scalar(select(func.count(JD.id)).where(JD.user_id.is_(None))) > 0
