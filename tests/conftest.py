"""pytest 全局配置：在导入 app 之前设置环境变量，使用独立临时 SQLite。

注意：环境变量必须在 `from app.main import app` 之前设置，
否则 db/llm/rate_limit 等模块在 import 期就按默认值（mysql/真LLM）初始化。
"""

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="ropt_test_")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ["DB_SQLITE_PATH"] = os.path.join(_TMP, "test.db")
os.environ["MOCK_LLM"] = "1"
os.environ["JWT_SECRET"] = "test-secret-0123456789abcdef0123456789abcdef"
os.environ["FREE_DAILY_QUOTA"] = "50"  # 常规测试不设限；配额测试直接改用户 daily_quota
os.environ["LOG_FORMAT"] = ""
os.environ["AUTH_IP_RATE_LIMIT"] = "100000"  # 测试期放开 /auth 的 IP 限流

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """整个测试会话共用一个 TestClient（lifespan 会执行 init_db 建表）。"""
    with TestClient(app) as c:
        yield c


def register_user(
    client,
    username=None,
    email=None,
    password="secret123",
    consent=True,
) -> dict:
    """走完整注册链路：send-code -> register（邮箱验证码）。返回常用字段。"""
    import uuid

    username = username or f"u{uuid.uuid4().hex[:10]}"
    email = email or f"{username}@example.com"
    r = client.post("/auth/send-code", json={"email": email, "purpose": "register"})
    assert r.status_code == 200, r.text
    code = (r.json().get("dev_code") or "") if r.json().get("dev") else "000000"
    assert code, "测试环境应走开发模式返回验证码"
    r = client.post(
        "/auth/register",
        json={
            "username": username,
            "password": password,
            "email": email,
            "code": code,
            "consent": consent,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    return {
        "username": username,
        "email": email,
        "token": data["token"],
        "headers": {"Authorization": f"Bearer {data['token']}"},
        "json": data,
    }


@pytest.fixture()
def new_user(client):
    """注册一个全新用户，返回 {username, email, token, headers, json}。"""
    return register_user(client)


@pytest.fixture()
def auth_headers(new_user):
    return new_user["headers"]



def make_user(client, username: str) -> dict:
    """注册并返回该用户名对应的用户（供配额测试直接改库用）。"""
    u = register_user(client, username=username)
    return {
        "id": u["json"]["user"]["id"],
        "username": username,
        "email": u["email"],
        "headers": u["headers"],
    }
