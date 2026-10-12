"""LangGraph Checkpointer 的 SQLAlchemy 持久化实现。

背景
----
原实现用 MemorySaver（进程内存）：重启丢对话、多实例部署不共享。
本模块把 LangGraph 的 checkpoint（含消息历史与 ChatState）序列化后存进
MySQL / SQLite，实现：
- 进程重启后多轮对话可恢复；
- 多实例部署共享同一份会话（DB 为真相源）；
- 数据随用户归属（user_id 列），账号删除时可一并清除。

线程安全（关键）
----------------
LangGraph 会在**线程池执行器**里调用 put_writes / get_tuple 等（与主调用线程、
FastAPI 请求线程并发）。为保证并发安全：
- 本模块使用**独立引擎 + 独立连接池**（build_engine()），绝不与请求级
  db_session 共享连接，避免 SQLite “transaction within transaction” 或原生崩溃；
- 所有 DB 读写用一把全局 RLock 串行化，杜绝跨线程对同一连接的竞争。
"""

import asyncio
import base64
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterator, Optional, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from sqlalchemy import desc, select
from sqlalchemy.orm import sessionmaker

from . import db as db_module
from .models import ChatCheckpoint, ChatWrite

logger = logging.getLogger(__name__)

# 全局锁：串行化 Checkpointer 的所有 DB 读写（LangGraph 会跨线程调用）
_lock = threading.RLock()

# 独立引擎 + 会话工厂（与业务请求的 db_session 完全隔离）
_cp_engine = None
_cp_sessionmaker = None


def _get_cp_sessionmaker():
    global _cp_engine, _cp_sessionmaker
    with _lock:
        if _cp_sessionmaker is None:
            _cp_engine = db_module.build_engine()
            _cp_sessionmaker = sessionmaker(
                bind=_cp_engine, autoflush=False, expire_on_commit=False
            )
        return _cp_sessionmaker


def _session():
    return _get_cp_sessionmaker()()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _encode(value: Any) -> str:
    """serde 序列化为 base64 JSON 字符串。"""
    ctype, cdata = value
    return json.dumps({"t": ctype, "d": _b64(cdata)})


def _decode(text: str) -> Any:
    obj = json.loads(text)
    return (obj["t"], _unb64(obj["d"]))


def _thread_ns(config: Optional[RunnableConfig]) -> tuple[str, str]:
    cfg = (config or {}).get("configurable", {}) or {}
    return cfg.get("thread_id", ""), cfg.get("checkpoint_ns", "") or ""


def _checkpoint_id(config: Optional[RunnableConfig]) -> Optional[str]:
    cfg = (config or {}).get("configurable", {}) or {}
    return cfg.get("checkpoint_id")


class SQLAlchemyCheckpointSaver(BaseCheckpointSaver):
    """把 checkpoint 持久化到 SQLAlchemy（MySQL/SQLite 通用，线程安全）。"""

    def __init__(self) -> None:
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

        super().__init__(serde=JsonPlusSerializer())

    # ---------------- 写入 ----------------
    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions,
    ) -> dict:
        thread_id, ns = _thread_ns(config)
        if not thread_id:
            raise ValueError("checkpoint 需要 configurable.thread_id")
        checkpoint_id = checkpoint["id"]
        parent_id = _checkpoint_id(config)
        user_id = (config.get("configurable", {}) or {}).get("user_id")

        payload = _encode(self.serde.dumps_typed(checkpoint))
        meta = _encode(self.serde.dumps_typed(metadata))
        now = datetime.now(timezone.utc)

        with _lock, _session() as s:
            row = s.scalar(
                select(ChatCheckpoint).where(
                    ChatCheckpoint.thread_id == thread_id,
                    ChatCheckpoint.checkpoint_ns == ns,
                    ChatCheckpoint.checkpoint_id == checkpoint_id,
                )
            )
            if row is None:
                s.add(
                    ChatCheckpoint(
                        thread_id=thread_id,
                        user_id=user_id,
                        checkpoint_ns=ns,
                        checkpoint_id=checkpoint_id,
                        payload=payload,
                        metadata_json=meta,
                        parent_checkpoint_id=parent_id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                row.payload = payload
                row.metadata_json = meta
                row.parent_checkpoint_id = parent_id
                if user_id is not None:
                    row.user_id = user_id
            s.commit()
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id, ns = _thread_ns(config)
        checkpoint_id = _checkpoint_id(config)
        if not checkpoint_id:
            return
        serialized = [
            {"channel": channel, "data": _encode(self.serde.dumps_typed(value))}
            for channel, value in writes
        ]
        with _lock, _session() as s:
            row = s.scalar(
                select(ChatWrite).where(
                    ChatWrite.thread_id == thread_id,
                    ChatWrite.checkpoint_ns == ns,
                    ChatWrite.checkpoint_id == checkpoint_id,
                    ChatWrite.task_id == task_id,
                    ChatWrite.task_path == task_path,
                )
            )
            if row is None:
                s.add(
                    ChatWrite(
                        thread_id=thread_id,
                        checkpoint_ns=ns,
                        checkpoint_id=checkpoint_id,
                        task_id=task_id,
                        task_path=task_path,
                        writes=json.dumps(serialized, ensure_ascii=False),
                    )
                )
            else:
                row.writes = json.dumps(serialized, ensure_ascii=False)
            s.commit()

    # ---------------- 读取 ----------------
    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id, ns = _thread_ns(config)
        cid = _checkpoint_id(config)
        with _lock, _session() as s:
            stmt = select(ChatCheckpoint).where(
                ChatCheckpoint.thread_id == thread_id,
                ChatCheckpoint.checkpoint_ns == ns,
            )
            if cid:
                stmt = stmt.where(ChatCheckpoint.checkpoint_id == cid)
            else:
                stmt = stmt.order_by(desc(ChatCheckpoint.id)).limit(1)
            row = s.scalar(stmt)
            if row is None:
                return None
            return self._row_to_tuple(s, row, cid)

    def list(
        self,
        config: Optional[RunnableConfig] = None,
        *,
        filter: Optional[dict] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        thread_id, ns = _thread_ns(config) if config else ("", None)
        with _lock, _session() as s:
            stmt = select(ChatCheckpoint)
            if config and thread_id:
                stmt = stmt.where(ChatCheckpoint.thread_id == thread_id)
            if config and ns:
                stmt = stmt.where(ChatCheckpoint.checkpoint_ns == ns)
            stmt = stmt.order_by(desc(ChatCheckpoint.id))
            before_id = _checkpoint_id(before) if before else None
            rows = s.scalars(stmt).all()
            for row in rows:
                if before_id and row.checkpoint_id >= before_id:
                    continue
                if filter is not None:
                    try:
                        meta = self.serde.loads_typed(_decode(row.metadata_json))
                    except Exception:  # noqa: BLE001
                        continue
                    if not all(meta.get(k) == v for k, v in filter.items()):
                        continue
                if limit is not None:
                    if limit <= 0:
                        break
                    limit -= 1
                yield self._row_to_tuple(s, row, row.checkpoint_id)

    def _row_to_tuple(self, s, row, requested_cid: Optional[str]) -> CheckpointTuple:
        checkpoint = self.serde.loads_typed(_decode(row.payload))
        metadata = self.serde.loads_typed(_decode(row.metadata_json))
        checkpoint = dict(checkpoint)
        if "channel_values" not in checkpoint:
            checkpoint["channel_values"] = {}

        write_rows = s.scalars(
            select(ChatWrite).where(
                ChatWrite.thread_id == row.thread_id,
                ChatWrite.checkpoint_ns == row.checkpoint_ns,
                ChatWrite.checkpoint_id == row.checkpoint_id,
            )
        ).all()
        pending_writes = []
        for wr in write_rows:
            try:
                items = json.loads(wr.writes)
            except json.JSONDecodeError:
                continue
            for it in items:
                try:
                    value = self.serde.loads_typed(_decode(it["data"]))
                except Exception:  # noqa: BLE001
                    continue
                pending_writes.append((wr.task_id, it["channel"], value))

        config = {
            "configurable": {
                "thread_id": row.thread_id,
                "checkpoint_ns": row.checkpoint_ns,
                "checkpoint_id": row.checkpoint_id,
            }
        }
        parent_config = None
        if row.parent_checkpoint_id:
            parent_config = {
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.parent_checkpoint_id,
                }
            }
        return CheckpointTuple(
            config=config,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes or None,
        )

    # ---------------- 异步委托 ----------------
    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def aget(self, config):
        return await asyncio.to_thread(self.get, config)

    async def alist(self, config=None, *, filter=None, before=None, limit=None):
        return [
            item
            async for item in self._alist_gen(config, filter=filter, before=before, limit=limit)
        ]

    async def _alist_gen(self, config, *, filter, before, limit) -> AsyncIterator[CheckpointTuple]:
        items = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for item in items:
            yield item


def get_db_checkpointer() -> SQLAlchemyCheckpointSaver:
    """返回 DB 持久化的 checkpointer（进程内复用单例）。"""
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = SQLAlchemyCheckpointSaver()
        logger.info("[checkpointer] 对话记忆使用 SQLAlchemy 独立连接持久化（线程安全、进程重启不丢）")
    return _checkpointer


_checkpointer: Optional[SQLAlchemyCheckpointSaver] = None
