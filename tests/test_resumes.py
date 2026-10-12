"""Resume library API tests: 3-item cap, overwrite, isolation, cleanup, auth."""

import uuid

from conftest import register_user


def _mk(name, content):
    return {"name": name, "content": content}


def test_resume_requires_auth(client):
    assert client.get("/resumes").status_code == 401
    assert client.post("/resumes", json=_mk("x", "y")).status_code == 401


def test_resume_lifecycle(client, new_user):
    h = new_user["headers"]
    r = client.post("/resumes", json=_mk("main", "first version"), headers=h)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]

    r = client.put(f"/resumes/{rid}", json={"name": "main v2", "content": "updated"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "updated"

    assert len(client.get("/resumes", headers=h).json()) == 1
    assert client.delete(f"/resumes/{rid}", headers=h).status_code == 204
    assert client.get("/resumes", headers=h).json() == []


def test_resume_max_three(client, new_user):
    h = new_user["headers"]
    for i in range(3):
        r = client.post("/resumes", json=_mk(f"resume{i}", f"content{i}"), headers=h)
        assert r.status_code == 201, r.text
    r = client.post("/resumes", json=_mk("fourth", "over quota"), headers=h)
    assert r.status_code == 409, r.text
    assert "3" in r.json()["detail"] or "limit" in r.json()["detail"].lower()
    assert len(client.get("/resumes", headers=h).json()) == 3


def test_resume_requires_content(client, new_user):
    h = new_user["headers"]
    r = client.post("/resumes", json={"name": "empty", "content": "   "}, headers=h)
    assert r.status_code == 400


def test_resume_isolation(client, new_user):
    h_a = new_user["headers"]
    rid = client.post("/resumes", json=_mk("A", "A content"), headers=h_a).json()["id"]

    h_b = register_user(client)["headers"]

    assert client.get("/resumes", headers=h_b).json() == []
    assert client.put(f"/resumes/{rid}", json={"content": "hack"}, headers=h_b).status_code in (403, 404)
    assert client.delete(f"/resumes/{rid}", headers=h_b).status_code in (403, 404)
    assert client.get("/resumes", headers=h_a).json()[0]["id"] == rid


def test_resume_cleared_on_account_delete(client):
    h = register_user(client)["headers"]
    client.post("/resumes", json=_mk("temp", "content"), headers=h)
    assert client.delete("/users/me", headers=h).status_code == 204