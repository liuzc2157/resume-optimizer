"""Checkpointer 单元测试（put/get_tuple/list/put_writes 往返）。"""

from app.checkpointer import SQLAlchemyCheckpointSaver
from app.db import init_db


def _cfg(thread_id, user_id=1):
    return {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": "",
            "user_id": user_id,
        }
    }


def test_put_and_get_roundtrip():
    init_db()
    saver = SQLAlchemyCheckpointSaver()
    cfg = _cfg("thread-unit-1")
    ckpt = {
        "v": 1,
        "id": "cp-001",
        "ts": "2026-01-01T00:00:00+00:00",
        "channel_values": {"messages": [{"role": "user", "content": "你好"}]},
        "channel_versions": {},
        "versions_seen": {},
        "updated_channels": None,
    }
    meta = {
        "source": "input",
        "step": 0,
        "parents": {},
        "run_id": "run-1",
        "counters_since_delta_snapshot": {},
    }
    ret = saver.put(cfg, ckpt, meta, {})
    assert ret["configurable"]["checkpoint_id"] == "cp-001"

    # 不带 checkpoint_id -> 取最新
    tup = saver.get_tuple(_cfg("thread-unit-1"))
    assert tup is not None
    assert tup.checkpoint["id"] == "cp-001"
    assert tup.checkpoint["channel_values"]["messages"][0]["content"] == "你好"

    # 带 checkpoint_id -> 精确取
    tup2 = saver.get_tuple(_cfg("thread-unit-1"))
    assert tup2.checkpoint["id"] == "cp-001"

    # list
    items = list(saver.list(_cfg("thread-unit-1")))
    assert len(items) == 1
    assert items[0].checkpoint["id"] == "cp-001"


def test_put_writes_roundtrip():
    init_db()
    saver = SQLAlchemyCheckpointSaver()
    cfg = _cfg("thread-unit-2")
    ckpt = {
        "v": 1,
        "id": "cp-002",
        "ts": "2026-01-01T00:00:00+00:00",
        "channel_values": {"messages": []},
        "channel_versions": {},
        "versions_seen": {},
        "updated_channels": None,
    }
    meta = {"source": "loop", "step": 1, "parents": {}, "run_id": "r", "counters_since_delta_snapshot": {}}
    # put 返回的 config 携带 checkpoint_id，put_writes 必须用它（真实 LangGraph 亦然）
    write_cfg = saver.put(cfg, ckpt, meta, {})
    saver.put_writes(write_cfg, [("messages", [{"role": "tool", "content": "done"}])], task_id="t1")

    tup = saver.get_tuple(cfg)
    assert tup is not None
    assert tup.pending_writes is not None
    found = [w for w in tup.pending_writes if w[0] == "t1" and w[1] == "messages"]
    assert found
    assert found[0][2] == [{"role": "tool", "content": "done"}]


def test_get_missing_thread_returns_none():
    init_db()
    saver = SQLAlchemyCheckpointSaver()
    assert saver.get_tuple(_cfg("thread-does-not-exist")) is None
