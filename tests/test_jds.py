"""JD 岗位库：归属隔离（种子全局可见，用户岗位仅本人可见/可改/可删）。"""

import uuid

from conftest import register_user


def _new_headers(client):
    return register_user(client)["headers"]


def test_jds_include_seed_plus_own(client):
    headers = _new_headers(client)
    r = client.post(
        "/jds",
        json={
            "position_category": "测试",
            "experience_level": "初级",
            "title": "我的测试岗位",
            "content": "这是我自己创建的岗位，测试工程师，负责自动化测试",
        },
        headers=headers,
    )
    assert r.status_code == 201
    my_id = r.json()["id"]

    listing = client.get("/jds", headers=headers).json()
    ids = [j["id"] for j in listing]
    assert my_id in ids
    # 种子岗位（数量大于 0）
    assert len(ids) > 1


def test_user_jd_not_visible_to_others(client):
    a = _new_headers(client)
    r = client.post(
        "/jds",
        json={
            "position_category": "安全",
            "experience_level": "高级",
            "title": "A的私有岗位",
            "content": "A 的安全岗位，渗透测试，漏洞挖掘",
        },
        headers=a,
    )
    assert r.status_code == 201
    a_private_id = r.json()["id"]

    b = _new_headers(client)
    listing_b = client.get("/jds", headers=b).json()
    assert a_private_id not in [j["id"] for j in listing_b]


def test_cannot_modify_or_delete_seed_jd(client):
    headers = _new_headers(client)
    seed_id = client.get("/jds", headers=headers).json()[0]["id"]
    r = client.delete(f"/jds/{seed_id}", headers=headers)
    assert r.status_code == 403
    r = client.put(
        f"/jds/{seed_id}",
        json={"title": "改种子"},
        headers=headers,
    )
    assert r.status_code == 403
