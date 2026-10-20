"""每日配额的**原子占用**回归测试（TOCTOU）。

原实现：请求开始时用 `count(usage_log)` 算余额，请求结束时才写 usage_log。
两个动作之间的窗口让并发请求同时读到「还有余额」而全部放行，
一个 10 次/天的免费档可以被一波并发刷到远超 10 次。

现实现：`quota_usage` 表按 (user_id, day) 唯一，用
`UPDATE ... WHERE used < quota` 的 rowcount 判定是否抢到名额，
把「检查」与「占用」合并成一次原子数据库操作。

说明：这里只做顺序断言（limit / 计数 / 历史用量对齐），并发验证见同目录说明——
SQLite 在 CI 上做多线程写会引入锁竞争噪音，并发场景改由部署后对线上服务压测确认。
"""

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import Base, QuotaUsage, UsageLog
from app.rate_limit import consume_quota, quota_remaining


@pytest.fixture()
def db():
    """独立的 SQLite 引擎，不碰 conftest 里那套应用级配置。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _user(uid: int = 1, quota: int = 3, plan: str = "free"):
    """consume_quota 只用到这三个属性，用轻量对象避免建整张 users 表。"""
    return SimpleNamespace(id=uid, daily_quota=quota, plan=plan)


class TestAtomicQuota:
    def test_allows_exactly_up_to_limit(self, db):
        user = _user(quota=3)
        for _ in range(3):
            consume_quota(db, user, "/optimize")

        with pytest.raises(HTTPException) as exc:
            consume_quota(db, user, "/optimize")
        assert exc.value.status_code == 429
        assert quota_remaining(db, user) == 0

    def test_remaining_counts_down(self, db):
        user = _user(quota=3)
        assert quota_remaining(db, user) == 3
        consume_quota(db, user, "/optimize")
        assert quota_remaining(db, user) == 2
        consume_quota(db, user, "/chat")
        assert quota_remaining(db, user) == 1

    def test_unlimited_never_blocks(self, db):
        # 注意：daily_quota=0 表示「按套餐默认」，不是不限量；
        # 真正不限量是 pro 套餐（get_daily_quota 返回 0）。
        user = _user(quota=None, plan="pro")
        for _ in range(20):
            consume_quota(db, user, "/optimize")
        assert quota_remaining(db, user) == -1
        # 不限量时不应写计数行
        assert db.scalar(select(func.count(QuotaUsage.id))) == 0

    def test_seeds_from_legacy_usage_log(self, db):
        """上线当天：老数据只有 usage_log，没有计数行，首占用必须把历史用量对齐进来。"""
        user = _user(quota=3)
        for _ in range(2):
            db.add(UsageLog(user_id=user.id, endpoint="/optimize"))
        db.commit()

        consume_quota(db, user, "/optimize")  # 2 + 1 = 3
        assert quota_remaining(db, user) == 0

        with pytest.raises(HTTPException):
            consume_quota(db, user, "/optimize")

    def test_legacy_rows_do_not_carry_to_next_day(self, db):
        """历史用量只在建行时对齐一次，跨天不应继续累计。"""
        user = _user(quota=2)
        consume_quota(db, user, "/optimize")
        consume_quota(db, user, "/optimize")
        assert quota_remaining(db, user) == 0

        # 模拟「新的一天」：把计数行的日期改成很久以前
        row = db.scalar(select(QuotaUsage).where(QuotaUsage.user_id == user.id))
        row.day = date(2000, 1, 1)
        db.commit()

        # 今天没有计数行了 -> 重新按 0 起算（usage_log 里也没有今天的配额接口记录）
        assert quota_remaining(db, user) == 2

    def test_different_users_are_isolated(self, db):
        a, b = _user(uid=1, quota=2), _user(uid=2, quota=2)
        consume_quota(db, a, "/optimize")
        consume_quota(db, a, "/optimize")
        with pytest.raises(HTTPException):
            consume_quota(db, a, "/optimize")
        # B 不受影响
        assert quota_remaining(db, b) == 2
