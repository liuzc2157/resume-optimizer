"""认证 / 账号管理路由：注册（邮箱验证码）、登录（用户名或邮箱）、找回密码、个人信息、注销。"""

import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from . import mailer
from .db import db_session
from .models import (
    AnalysisCache,
    ChatCheckpoint,
    ChatWrite,
    EmailCode,
    JD,
    SavedResume,
    UsageLog,
    User,
)
from .rate_limit import get_daily_quota, ip_rate_limit, quota_remaining
from .schemas import (
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SendCodeRequest,
    SendCodeResponse,
    TokenResponse,
    UserMeResponse,
    UserProfile,
)
from .security import (
    create_token,
    get_current_user,
    hash_password,
    verify_password,
)
from .usage import record_usage, usage_summary

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

# 邮箱格式校验（够用即可，不引第三方依赖）
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

# 验证码有效期（秒）/ 重发冷却（秒）/ 最大错误次数
CODE_TTL_SECONDS = int(os.getenv("CODE_TTL_SECONDS", "600"))
CODE_RESEND_COOLDOWN = int(os.getenv("CODE_RESEND_COOLDOWN", "60"))
CODE_MAX_ATTEMPTS = 5


def _utcnow() -> datetime:
    """SQLite/MySQL 存的是 naive UTC 字符串，统一用 naive UTC 比较。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email or ""))


def _issue_code(s: Session, email: str, purpose: str) -> str:
    """生成 6 位数字验证码并落库（同一邮箱同一用途旧码作废，只留最新一条）。"""
    now = _utcnow()
    for row in s.scalars(
        select(EmailCode).where(EmailCode.email == email, EmailCode.purpose == purpose)
    ).all():
        row.used_at = now
    code = f"{secrets.randbelow(1_000_000):06d}"
    s.add(
        EmailCode(
            email=email,
            purpose=purpose,
            code_hash=hashlib.sha256(code.encode()).hexdigest(),
            expires_at=now + timedelta(seconds=CODE_TTL_SECONDS),
            created_at=now,
        )
    )
    s.commit()
    return code


def _cooldown_remaining(s: Session, email: str, purpose: str) -> int:
    """距离上次发码的剩余冷却秒数（0 = 立即可发）。"""
    row = s.scalar(
        select(EmailCode)
        .where(EmailCode.email == email, EmailCode.purpose == purpose)
        .order_by(EmailCode.created_at.desc())
    )
    if row is None or row.created_at is None:
        return 0
    elapsed = (_utcnow() - row.created_at).total_seconds()
    return max(0, int(CODE_RESEND_COOLDOWN - elapsed))


def _verify_code(s: Session, email: str, purpose: str, code: str) -> str:
    """校验验证码：成功返回 'ok'，失败返回原因文案（校验成功即作废，一次性）。"""
    row = s.scalar(
        select(EmailCode)
        .where(EmailCode.email == email, EmailCode.purpose == purpose, EmailCode.used_at.is_(None))
        .order_by(EmailCode.created_at.desc())
    )
    if row is None:
        return "验证码不存在或已使用，请重新获取"
    if row.expires_at is not None and row.expires_at < _utcnow():
        return "验证码已过期，请重新获取"
    if row.attempts >= CODE_MAX_ATTEMPTS:
        return "尝试次数过多，验证码已失效，请重新获取"
    expected = hashlib.sha256(code.strip().encode()).hexdigest()
    if not secrets.compare_digest(row.code_hash, expected):
        row.attempts += 1
        s.commit()
        return "验证码错误，请重新输入"
    row.used_at = _utcnow()
    s.commit()
    return "ok"


def _send_code_impl(s: Session, email: str, purpose: str) -> SendCodeResponse:
    """发验证码的核心逻辑（注册 / 找回共用）。"""
    email = email.strip().lower()
    if not _valid_email(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    if purpose == "register":
        exists = s.scalar(select(User).where(User.email == email))
        if exists is not None:
            raise HTTPException(status_code=409, detail="该邮箱已注册，请直接登录或使用「忘记密码」")
    elif purpose == "reset":
        user = s.scalar(select(User).where(User.email == email))
        if user is None:
            # 不泄露邮箱是否注册：统一返回成功（不发码），提示去注册
            return SendCodeResponse(ok=True, dev=False, expires_in=CODE_TTL_SECONDS, cooldown_seconds=0)
    cool = _cooldown_remaining(s, email, purpose)
    if cool > 0:
        raise HTTPException(status_code=429, detail=f"发送过于频繁，请 {cool} 秒后再试")
    code = _issue_code(s, email, purpose)
    if mailer.smtp_configured():
        subject = "注册验证码" if purpose == "register" else "重置密码验证码"
        mailer.send_email(
            email,
            f"【简遇】{subject}",
            f"你的验证码是 {code}，{CODE_TTL_SECONDS // 60} 分钟内有效，请勿泄露。如非本人操作请忽略本邮件。",
        )
        return SendCodeResponse(ok=True, dev=False, expires_in=CODE_TTL_SECONDS, cooldown_seconds=CODE_RESEND_COOLDOWN)
    # 开发模式（未配 SMTP）：直接回传验证码，方便本地联调
    logger.info("[auth] 开发模式下发验证码 -> %s（%s）：%s", email, purpose, code)
    return SendCodeResponse(
        ok=True, dev=True, dev_code=code, expires_in=CODE_TTL_SECONDS, cooldown_seconds=CODE_RESEND_COOLDOWN
    )


@router.post("/auth/send-code", response_model=SendCodeResponse)
def send_code(
    req: SendCodeRequest,
    s: Session = Depends(db_session),
    _: None = Depends(ip_rate_limit),
) -> SendCodeResponse:
    """发送邮箱验证码。purpose=register 注册 / purpose=reset 找回密码。"""
    purpose = (req.purpose or "register").strip().lower()
    if purpose not in ("register", "reset"):
        raise HTTPException(status_code=400, detail="purpose 只能是 register 或 reset")
    return _send_code_impl(s, req.email, purpose)


@router.post("/auth/forgot-password", response_model=SendCodeResponse)
def forgot_password(
    req: SendCodeRequest,
    s: Session = Depends(db_session),
    _: None = Depends(ip_rate_limit),
) -> SendCodeResponse:
    """忘记密码：向注册邮箱发送重置验证码（等价于 send-code purpose=reset）。"""
    return _send_code_impl(s, req.email, "reset")


@router.post("/auth/reset-password", response_model=TokenResponse)
def reset_password(
    req: ResetPasswordRequest,
    s: Session = Depends(db_session),
    _: None = Depends(ip_rate_limit),
) -> TokenResponse:
    """用验证码重置密码：校验通过即改密并返回新令牌（前端直接登录）。"""
    email = req.email.strip().lower()
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")
    check = _verify_code(s, email, "reset", req.code)
    if check != "ok":
        raise HTTPException(status_code=400, detail=check)
    user = s.scalar(select(User).where(User.email == email))
    if user is None:
        raise HTTPException(status_code=404, detail="账号不存在，请先注册")
    user.password_hash = hash_password(req.new_password)
    user.last_login_at = datetime.now(timezone.utc)
    s.commit()
    logger.info("[auth] 用户重置密码：%s (id=%d)", user.username, user.id)
    record_usage(s, user.id, "/auth/reset-password", cache_hit=False)
    return TokenResponse(token=create_token(user.id), token_type="bearer", user=_profile(user))


@router.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    req: RegisterRequest,
    s: Session = Depends(db_session),
    _: None = Depends(ip_rate_limit),
) -> TokenResponse:
    """注册新账号：邮箱 + 验证码 + 同意隐私政策。一个邮箱只能注册一个账号。"""
    username = req.username.strip()
    email = req.email.strip().lower()
    if len(username) < 2 or len(username) > 64:
        raise HTTPException(status_code=400, detail="用户名长度需在 2-64 个字符之间")
    if not _valid_email(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    if not req.consent:
        raise HTTPException(
            status_code=400,
            detail="请先阅读并勾选同意《隐私政策》，才能注册使用（简历属于个人信息，我们只用于优化目的并支持随时删除）。",
        )
    check = _verify_code(s, email, "register", req.code)
    if check != "ok":
        raise HTTPException(status_code=400, detail=check)
    exists = s.scalar(select(User).where(User.username == username))
    if exists is not None:
        raise HTTPException(status_code=409, detail="用户名已被占用，请换一个")
    email_exists = s.scalar(select(User).where(User.email == email))
    if email_exists is not None:
        raise HTTPException(status_code=409, detail="该邮箱已注册，一个邮箱只能注册一个账号")

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(req.password),
        plan="free",
        daily_quota=0,
        is_active=True,
        consent_at=datetime.now(timezone.utc),
    )
    s.add(user)
    s.commit()
    s.refresh(user)
    logger.info("[auth] 新用户注册：%s (id=%d) email=%s", user.username, user.id, user.email)
    record_usage(s, user.id, "/auth/register", cache_hit=False)
    return TokenResponse(token=create_token(user.id), token_type="bearer", user=_profile(user))


@router.post("/auth/login", response_model=TokenResponse)
def login(
    req: LoginRequest,
    s: Session = Depends(db_session),
    _: None = Depends(ip_rate_limit),
) -> TokenResponse:
    """登录：用户名或邮箱 + 密码，返回访问令牌。"""
    account = req.account.strip()
    user = s.scalar(
        select(User).where(or_(User.username == account, User.email == account.lower()))
    )
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名/邮箱或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用")
    user.last_login_at = datetime.now(timezone.utc)
    s.commit()
    logger.info("[auth] 用户登录：%s (id=%d)", user.username, user.id)
    record_usage(s, user.id, "/auth/login", cache_hit=False)
    return TokenResponse(token=create_token(user.id), token_type="bearer", user=_profile(user))


@router.get("/users/me", response_model=UserMeResponse)
def me(
    user: User = Depends(get_current_user),
    s: Session = Depends(db_session),
) -> UserMeResponse:
    """当前用户信息 + 配额 + 用量（前端顶栏展示用）。"""
    remaining = quota_remaining(s, user)
    return UserMeResponse(
        profile=_profile(user),
        quota={
            "daily": get_daily_quota(user),
            "remaining": remaining,
            "unlimited": remaining == -1,
        },
        usage=usage_summary(s, user.id),
    )


@router.delete("/users/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_me(
    user: User = Depends(get_current_user),
    s: Session = Depends(db_session),
) -> None:
    """注销账号：删除该用户的全部数据（简历分析缓存、会话、用量记录、自建 JD）。

    对应「删除权」——简历属于个人信息，用户有权要求彻底清除。
    种子岗位（user_id IS NULL）不在此列，仍全局共享。
    """
    user_id = user.id
    # 该用户的对话线程
    thread_ids = s.scalars(
        select(ChatCheckpoint.thread_id).where(ChatCheckpoint.user_id == user_id)
    ).all()
    # 用户自建的 JD
    own_jds = s.scalars(select(JD.id).where(JD.user_id == user_id)).all()
    for jd_id in own_jds:
        try:
            from . import rag

            rag.delete_jd(jd_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[auth] 注销时清除 JD#%s 向量失败（可忽略）：%s", jd_id, exc)

    # 清除各表数据
    s.execute(delete(ChatWrite).where(ChatWrite.thread_id.in_(thread_ids)))
    s.execute(delete(ChatCheckpoint).where(ChatCheckpoint.user_id == user_id))
    s.execute(delete(UsageLog).where(UsageLog.user_id == user_id))
    s.execute(delete(AnalysisCache).where(AnalysisCache.user_id == user_id))
    s.execute(delete(JD).where(JD.user_id == user_id))
    s.execute(delete(SavedResume).where(SavedResume.user_id == user_id))
    s.delete(user)
    s.commit()
    logger.info("[auth] 用户已注销并清除全部数据：%s (id=%d)", user.username, user_id)


def _profile(user: User) -> UserProfile:
    return UserProfile(
        id=user.id,
        username=user.username,
        email=user.email,
        plan=user.plan,
        daily_quota=user.daily_quota,
        consent_at=user.consent_at,
        created_at=user.created_at,
    )
