"""限流 + 配额模块。

两层防护，职责分开：
1. 滑动窗口限流（进程内）：限制单用户在单位时间内的请求频率，
   防止脚本刷接口、把 LLM 费用刷爆。默认 30 次/分钟/用户，可配。
2. 每日配额（数据库）：基于 usage_log 统计今天的调用次数，
   达到 daily_quota 后返回 429。免费档默认 10 次/天，可配。

对外暴露两个 FastAPI 依赖：
- enforce_rate_limit：用于所有业务接口（内部先鉴权，返回当前 User）。
- ip_rate_limit：用于 /auth 的注册/登录，防爆破（按 IP）。
"""

import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from typing import Deque, Tuple

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import db_session
from .models import QuotaUsage, UsageLog, User
from .security import get_current_user

logger = logging.getLogger(__name__)

# 每用户每分钟最大请求数（进程内窗口）
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
# /auth 注册/登录按 IP 限流（防爆破），测试环境可调高
AUTH_IP_RATE_LIMIT = int(os.getenv("AUTH_IP_RATE_LIMIT", "20"))
# 免费档每日默认配额（可被 User.daily_quota 覆盖；0 = 不限）
FREE_DAILY_QUOTA = int(os.getenv("FREE_DAILY_QUOTA", "10"))
# 哪些接口消耗每日配额（按 endpoint 名匹配）
# 注意：/match 只在 use_rag=True 时消耗配额（会调 LLM 提炼检索词），
# 纯关键词匹配属于零成本计算，接口会以 "/match/keyword" 单独记账，不计入配额。
QUOTA_ENDPOINTS = {"/optimize", "/chat", "/compare", "/match", "/rag/retrieve"}


class SlidingWindowLimiter:
    """进程内滑动窗口限流器（线程安全）。"""

    def __init__(self, max_requests: int, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> Tuple[bool, float]:
        """返回 (是否放行, 建议等待秒数)。"""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            cutoff = now - self.window_seconds
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_requests:
                retry = max(0.0, self.window_seconds - (now - q[0]))
                return False, retry
            q.append(now)
            return True, 0.0


# 业务接口：按用户限流；认证接口：按 IP 限流
_user_limiter = SlidingWindowLimiter(RATE_LIMIT_PER_MINUTE, 60.0)
_ip_limiter = SlidingWindowLimiter(AUTH_IP_RATE_LIMIT, 60.0)


def _today() -> date:
    """今天（UTC，与数据库 server_default=now() 的时间口径一致）。"""
    return datetime.now(timezone.utc).date()


def _legacy_daily_used(s: Session, user_id: int) -> int:
    """按 usage_log 统计今天已用的配额单位数。

    只在两个地方用：① 当天第一次占用配额时，把历史用量一次性对齐进计数器
    （避免上线当天给用户凭空多出一次额度）；② 计数行还没建立时给
    /users/me 兜底展示。
    """
    today = func.date(func.now())
    stmt = (
        select(func.count(UsageLog.id))
        .where(UsageLog.user_id == user_id)
        .where(UsageLog.endpoint.in_(QUOTA_ENDPOINTS))
        .where(func.date(UsageLog.created_at) == today)
    )
    return int(s.scalar(stmt) or 0)


def _used_today(s: Session, user_id: int) -> int:
    row = s.scalar(
        select(QuotaUsage).where(QuotaUsage.user_id == user_id, QuotaUsage.day == _today())
    )
    if row is not None:
        return int(row.used or 0)
    return _legacy_daily_used(s, user_id)


def get_daily_quota(user: User) -> int:
    """用户每日配额：账号定制 > 环境默认免费档。0 表示不限。"""
    if user.daily_quota and user.daily_quota > 0:
        return user.daily_quota
    if user.plan == "pro":
        return 0
    return FREE_DAILY_QUOTA


def quota_remaining(s: Session, user: User) -> int:
    """剩余配额（-1 表示不限）。"""
    quota = get_daily_quota(user)
    if quota <= 0:
        return -1
    return max(0, quota - _used_today(s, user.id))


def _quota_exhausted() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="今日免费配额已用完，请明天再试（或联系开通更多额度）",
    )


def consume_quota(s: Session, user: User, endpoint: str) -> None:
    """原子占用一次每日配额；超出上限抛 429。

    为什么需要它：原实现是「请求开始时读 usage_log 算余额、请求结束时才写用量」，
    两个动作之间存在窗口，并发请求会同时看到「还有余额」而全部放行（TOCTOU），
    一个 10 次/天的免费档可以被一波并发刷到远超 10 次。

    这里把「检查」与「占用」合并成一次条件更新：`UPDATE ... WHERE used < quota`，
    只有 rowcount=1 才算抢到名额；数据库保证这一步是原子的。
    """
    quota = get_daily_quota(user)
    if quota <= 0:  # 不限量
        return
    day = _today()

    # 1) 计数行已存在：还有余额时自增才生效
    res = s.execute(
        update(QuotaUsage)
        .where(
            QuotaUsage.user_id == user.id,
            QuotaUsage.day == day,
            QuotaUsage.used < quota,
        )
        .values(used=QuotaUsage.used + 1)
    )
    s.commit()
    if res.rowcount:
        logger.debug("[quota] %s 占用一次配额（endpoint=%s）", user.id, endpoint)
        return

    # 2) 行已存在但已满 -> 拒绝
    row = s.scalar(
        select(QuotaUsage).where(QuotaUsage.user_id == user.id, QuotaUsage.day == day)
    )
    if row is not None:
        raise _quota_exhausted()

    # 3) 今天还没有计数行：建行并把历史用量对齐进来
    seed = _legacy_daily_used(s, user.id)
    if seed >= quota:
        raise _quota_exhausted()
    try:
        s.add(QuotaUsage(user_id=user.id, day=day, used=seed + 1))
        s.commit()
        logger.debug("[quota] %s 建立今日计数行（seed=%d）", user.id, seed)
    except IntegrityError:
        # 并发下别的请求先建了行 -> 退化为条件自增
        s.rollback()
        res = s.execute(
            update(QuotaUsage)
            .where(
                QuotaUsage.user_id == user.id,
                QuotaUsage.day == day,
                QuotaUsage.used < quota,
            )
            .values(used=QuotaUsage.used + 1)
        )
        s.commit()
        if not res.rowcount:
            raise _quota_exhausted() from None


def enforce_rate_limit(
    user: User = Depends(get_current_user),
    s: Session = Depends(db_session),
) -> User:
    """业务接口限流依赖：先鉴权、再按用户滑动窗口限流，超频抛 429。

    这里**不再**做配额预检：配额改由 consume_quota() 在真正会调 LLM 的 handler
    里原子占用。放在依赖里既拦不准（/match 是否消耗配额取决于请求体的 use_rag），
    又会误伤不消耗配额的接口（配额用尽时连岗位库、简历库都读不了）。
    """
    ok, retry = _user_limiter.allow(f"user:{user.id}")
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"请求过于频繁，请 {int(retry) + 1} 秒后再试",
            headers={"Retry-After": str(int(retry) + 1)},
        )
    return user


def ip_rate_limit(request: Request) -> None:
    """认证接口限流：按客户端 IP 限制频率（防爆破）。"""
    ip = request.client.host if request.client else "unknown"
    ok, retry = _ip_limiter.allow(f"ip:{ip}")
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"尝试过于频繁，请 {int(retry) + 1} 秒后再试",
            headers={"Retry-After": str(int(retry) + 1)},
        )
