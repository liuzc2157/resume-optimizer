"""安全模块：密码哈希 + JWT 签发/校验 + FastAPI 鉴权依赖。

设计要点
--------
- 密码用标准库 PBKDF2-HMAC-SHA256 加盐哈希（无第三方依赖、无 bcrypt 兼容坑），
  迭代次数 26 万，与 OWASP 推荐量级一致。
- 令牌用 PyJWT (HS256)，默认 7 天有效。
- JWT_SECRET：生产必须显式设置；未设置时退化为进程内随机密钥并打警告
  （重启后旧令牌失效，仅适合本地开发，绝不用于生产）。
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .db import db_session
from .models import User

logger = logging.getLogger(__name__)

_PBKDF2_ITERATIONS = 260_000
_SALT_BYTES = 16

JWT_ALG = "HS256"
JWT_TTL_SECONDS = int(os.getenv("JWT_TTL_SECONDS", str(7 * 24 * 3600)))

# JWT 密钥：优先环境变量；本地开发用进程内随机密钥（重启即失效，会打警告）
_JWT_SECRET: str = os.getenv("JWT_SECRET", "")
if not _JWT_SECRET:
    _JWT_SECRET = secrets.token_urlsafe(48)
    logger.warning(
        "[security] 未设置 JWT_SECRET，使用进程内随机密钥。生产环境必须设置 JWT_SECRET，"
        "否则服务重启后所有登录态失效、且多实例之间无法互认令牌。"
    )


# ---------------- 密码哈希 ----------------
def hash_password(password: str) -> str:
    """PBKDF2 加盐哈希，返回 'iterations$salt_b64$hash_b64'。"""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return (
        f"{_PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}"
        f"${base64.b64encode(digest).decode()}"
    )


def verify_password(password: str, stored: str) -> bool:
    """常数时间校验密码（用 hmac.compare_digest 防时序攻击）。"""
    try:
        iterations_s, salt_b64, hash_b64 = stored.split("$")
        iterations = int(iterations_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


# ---------------- JWT ----------------
def create_token(user_id: int) -> str:
    """为用户签发 JWT 访问令牌。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(seconds=JWT_TTL_SECONDS),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str) -> Optional[int]:
    """校验并解码 JWT，返回 user_id；无效/过期返回 None。"""
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[JWT_ALG])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


# ---------------- FastAPI 依赖 ----------------
def get_current_user(
    authorization: Optional[str] = Header(default=None),
    s: Session = Depends(db_session),
) -> User:
    """FastAPI 依赖：解析 Authorization: Bearer <token>，返回当前用户。

    失败统一抛 401，并给出可读的中文提示。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录：请先调用 /auth/login 获取令牌，并在请求头携带 Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:].strip()
    user_id = decode_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已过期或令牌无效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = s.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号不存在或已被禁用",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def is_admin_user(user: User) -> bool:
    """是否为管理员（ADMIN_USERNAMES 逗号分隔，本地默认 root）。"""
    admins = {
        a.strip()
        for a in os.getenv("ADMIN_USERNAMES", "root").split(",")
        if a.strip()
    }
    return user.username in admins
