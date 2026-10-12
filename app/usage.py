"""用量统计：按请求聚合 LLM token 用量并落库。

实现方式
--------
- usage_scope() 作为 FastAPI 依赖，为每次请求生成一个**独立的用量桶**（普通 dict），
  由接口显式传给 service / nodes / llm 逐层透传累加。
  （不用 ContextVar/thread-local：FastAPI 同步接口跑在线程池里，跨线程的
  上下文传递不可靠，显式透传最稳、也最容易测试。）
- llm.py 在真实调用成功后调用 accumulate_usage(bucket, ...) 累加 token。
- 业务接口处理完后，把用量桶写入 usage_log，同时用于配额与审计。

对外
----
- usage_scope()       FastAPI 依赖：返回当前请求的空用量桶
- accumulate_usage()  由 llm.py 调用，累加 token 到指定桶
- record_usage()      把用量桶写入 usage_log
- usage_summary()     汇总某用户的用量（供 /users/me 展示）
"""

import logging
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import db_session
from .models import UsageLog

logger = logging.getLogger(__name__)


def usage_scope() -> Dict[str, Any]:
    """FastAPI 依赖：返回当前请求的用量桶（每次请求独立新建）。"""
    return {
        "llm_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "model": None,
    }


def accumulate_usage(
    bucket: Optional[Dict[str, Any]],
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    model: Optional[str] = None,
) -> None:
    """llm.py 在真实调用成功后调用：把 token 用量累加进指定桶。"""
    if bucket is None:
        return
    bucket["llm_calls"] = int(bucket.get("llm_calls", 0) or 0) + 1
    bucket["prompt_tokens"] = int(bucket.get("prompt_tokens", 0) or 0) + int(prompt_tokens or 0)
    bucket["completion_tokens"] = int(bucket.get("completion_tokens", 0) or 0) + int(completion_tokens or 0)
    if model:
        bucket["model"] = model


def record_usage(
    s: Session,
    user_id: int,
    endpoint: str,
    cache_hit: bool = False,
    bucket: Optional[Dict[str, Any]] = None,
) -> None:
    """把一次接口调用写入 usage_log。bucket 为空时按全零记录。"""
    bucket = bucket or {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "model": None}
    s.add(
        UsageLog(
            user_id=user_id,
            endpoint=endpoint,
            cache_hit=bool(cache_hit),
            llm_calls=int(bucket.get("llm_calls", 0) or 0),
            prompt_tokens=int(bucket.get("prompt_tokens", 0) or 0),
            completion_tokens=int(bucket.get("completion_tokens", 0) or 0),
            model=bucket.get("model"),
        )
    )
    s.commit()


def usage_summary(s: Session, user_id: int) -> Dict[str, Any]:
    """汇总某用户的用量（今天 + 累计）。"""
    today = func.date(func.now())
    rows = s.execute(
        select(
            UsageLog.endpoint,
            func.count(UsageLog.id),
            func.sum(UsageLog.prompt_tokens),
            func.sum(UsageLog.completion_tokens),
        )
        .where(UsageLog.user_id == user_id)
        .group_by(UsageLog.endpoint)
    ).all()
    total_llm = sum(int(r[1] or 0) for r in rows)
    total_prompt = sum(int(r[2] or 0) for r in rows)
    total_completion = sum(int(r[3] or 0) for r in rows)

    today_rows = s.execute(
        select(func.count(UsageLog.id))
        .where(UsageLog.user_id == user_id)
        .where(func.date(UsageLog.created_at) == today)
    ).scalar() or 0

    by_endpoint = {r[0]: int(r[1] or 0) for r in rows}
    return {
        "total_calls": total_llm,
        "today_calls": int(today_rows or 0),
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "total_tokens": total_prompt + total_completion,
        "by_endpoint": by_endpoint,
    }
