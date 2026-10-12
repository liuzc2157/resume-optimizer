"""认证与账号管理测试（邮箱验证码注册 / 登录 / 找回密码）。"""

from conftest import make_user, register_user


def test_health_public(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_business_endpoint_requires_auth(client):
    r = client.post(
        "/optimize",
        json={"resume_text": "Python 后端", "jd_text": "招聘 Python 后端"},
    )
    assert r.status_code == 401


def test_register_requires_consent(client):
    r = client.post(
        "/auth/register",
        json={"username": "nocon", "password": "secret123", "email": "nocon@example.com", "code": "123456"},
    )
    assert r.status_code == 400
    assert "隐私" in r.json()["detail"]


def test_register_requires_valid_email(client):
    r = client.post(
        "/auth/register",
        json={"username": "badmail", "password": "secret123", "email": "not-an-email", "code": "123456", "consent": True},
    )
    assert r.status_code == 400


def test_register_requires_code(client):
    r = client.post(
        "/auth/register",
        json={"username": "nocode", "password": "secret123", "email": "nocode@example.com", "consent": True},
    )
    assert r.status_code in (400, 422)


def test_register_wrong_code_rejected(client):
    r = client.post("/auth/send-code", json={"email": "wrongcode@example.com", "purpose": "register"})
    assert r.status_code == 200
    r = client.post(
        "/auth/register",
        json={"username": "wrongcode", "password": "secret123", "email": "wrongcode@example.com", "code": "000000", "consent": True},
    )
    assert r.status_code == 400


def test_duplicate_email_rejected(client):
    register_user(client, username="a1", email="same@example.com")
    # 同一邮箱再次注册 -> 409（send-code 阶段即拦截）
    r = client.post("/auth/send-code", json={"email": "same@example.com", "purpose": "register"})
    assert r.status_code == 409


def test_register_and_login_flow(client, new_user):
    # 注册后带令牌可直接访问
    r = client.get("/users/me", headers=new_user["headers"])
    assert r.status_code == 200
    assert r.json()["profile"]["username"] == new_user["username"]
    assert r.json()["profile"]["email"] == new_user["email"]

    # 用用户名登录
    r = client.post(
        "/auth/login", json={"account": new_user["username"], "password": "secret123"}
    )
    assert r.status_code == 200
    assert r.json()["token"]

    # 用邮箱也能登录
    r = client.post(
        "/auth/login", json={"account": new_user["email"], "password": "secret123"}
    )
    assert r.status_code == 200
    assert r.json()["token"]


def test_duplicate_username_409(client):
    make_user(client, "dup")
    r = client.post("/auth/send-code", json={"email": "dup2@example.com", "purpose": "register"})
    code = r.json()["dev_code"]
    r = client.post(
        "/auth/register",
        json={"username": "dup", "password": "secret123", "email": "dup2@example.com", "code": code, "consent": True},
    )
    assert r.status_code == 409


def test_wrong_password_401(client, new_user):
    r = client.post(
        "/auth/login", json={"account": new_user["username"], "password": "wrongpass"}
    )
    assert r.status_code == 401


def test_invalid_token_401(client):
    r = client.get("/users/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401


def test_short_password_rejected(client):
    r = client.post(
        "/auth/register",
        json={"username": "short", "password": "123", "email": "short@example.com", "code": "123456", "consent": True},
    )
    assert r.status_code == 400


def test_forgot_password_reset_flow(client):
    u = register_user(client, username="resetuser", email="reset@example.com")
    # 老密码能登录
    r = client.post("/auth/login", json={"account": "reset@example.com", "password": "secret123"})
    assert r.status_code == 200

    # 发重置码（开发模式直接返回）
    r = client.post("/auth/forgot-password", json={"email": "reset@example.com"})
    assert r.status_code == 200
    data = r.json()
    assert data["dev"] is True
    code = data["dev_code"]

    # 错误验证码 -> 400
    r = client.post(
        "/auth/reset-password",
        json={"email": "reset@example.com", "code": "000000", "new_password": "newpass123"},
    )
    assert r.status_code == 400

    # 正确验证码 -> 改密并返回令牌
    r = client.post(
        "/auth/reset-password",
        json={"email": "reset@example.com", "code": code, "new_password": "newpass123"},
    )
    assert r.status_code == 200
    assert r.json()["token"]

    # 新密码可登录、旧密码不可
    r = client.post("/auth/login", json={"account": "reset@example.com", "password": "newpass123"})
    assert r.status_code == 200
    r = client.post("/auth/login", json={"account": "reset@example.com", "password": "secret123"})
    assert r.status_code == 401


def test_reset_code_single_use(client):
    u = register_user(client, username="once", email="once@example.com")
    r = client.post("/auth/forgot-password", json={"email": "once@example.com"})
    code = r.json()["dev_code"]
    r = client.post(
        "/auth/reset-password",
        json={"email": "once@example.com", "code": code, "new_password": "once123"},
    )
    assert r.status_code == 200
    # 同一验证码复用 -> 拒绝
    r = client.post(
        "/auth/reset-password",
        json={"email": "once@example.com", "code": code, "new_password": "once456"},
    )
    assert r.status_code == 400
