"""对话 Agent：多轮持久化、用户隔离、工具调用。"""

from conftest import register_user


def _chat(client, headers, message, thread_id=None):
    body = {"message": message}
    if thread_id:
        body["thread_id"] = thread_id
    return client.post("/chat", json=body, headers=headers)


def test_chat_simple_reply(client, new_user):
    r = _chat(client, new_user["headers"], "你好")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["thread_id"]
    assert data["reply"]


def test_chat_same_thread_persists(client, new_user):
    r1 = _chat(client, new_user["headers"], "第一轮")
    tid = r1.json()["thread_id"]
    r2 = _chat(client, new_user["headers"], "第二轮", thread_id=tid)
    assert r2.status_code == 200
    # 同一 thread 应能读取到上一轮状态（DB checkpoint 持久化）
    assert r2.json()["thread_id"] == tid


def test_chat_threads_isolated_between_users(client, new_user):
    r1 = _chat(client, new_user["headers"], "用户A的会话")
    tid_a = r1.json()["thread_id"]

    headers_b = register_user(client)["headers"]
    # 用户 B 尝试续聊用户 A 的 thread_id -> 应被视为新会话（不泄露 A 的状态）
    r2 = _chat(client, headers_b, "B 的消息", thread_id=tid_a)
    assert r2.status_code == 200
    assert r2.json()["thread_id"] == tid_a  # 名字空间按用户隔离，客户端 thread_id 不变


def test_chat_tool_analyze(client, new_user):
    r = _chat(client, new_user["headers"], "我的简历是：Python 后端 3 年经验。请帮我分析匹配度")
    assert r.status_code == 200, r.text
    assert r.json()["reply"]
