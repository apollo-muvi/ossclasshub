"""Basic smoke tests for ClassHub OSS API (single-instance, no tenant)"""
import asyncio

import httpx
import pytest


class ASGITestClient:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path, **kwargs):
        return self.request("PUT", path, **kwargs)

    def delete(self, path, **kwargs):
        return self.request("DELETE", path, **kwargs)


@pytest.fixture
def client():
    import os
    import shutil
    os.environ["CLASSHUB_DB"] = "/tmp/classhub-oss-test.db"
    os.environ["CLASSHUB_SECRET"] = "test-secret"
    os.environ["CLASSHUB_UPLOAD_DIR"] = "/tmp/classhub-oss-test-uploads"
    os.environ["CLASSHUB_IMAGE_STORAGE_PROVIDER"] = "local"
    try:
        os.unlink("/tmp/classhub-oss-test.db")
    except FileNotFoundError:
        pass
    shutil.rmtree("/tmp/classhub-oss-test-uploads", ignore_errors=True)
    import app.config
    app.config._settings = None
    import app.db
    app.db._db = None
    import app.storage
    app.storage._registry = None
    import importlib
    importlib.reload(app.config)
    importlib.reload(app.db)
    importlib.reload(app.storage)
    import app.main
    importlib.reload(app.main)
    from app.main import app as fresh_app
    return ASGITestClient(fresh_app)


def _setup_and_login(client):
    r = client.post("/api/v1/setup", json={"username": "admin", "password": "test12345"})
    assert r.status_code == 200
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "test12345"})
    assert r.status_code == 200
    return r.json()["token"]


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_app_settings(client):
    r = client.get("/api/v1/app-settings")
    assert r.status_code == 200
    assert "api_version" in r.json()


def test_setup_and_login(client):
    _setup_and_login(client)
    # duplicate setup
    r = client.post("/api/v1/setup", json={"username": "admin2", "password": "test12345"})
    assert r.status_code == 409
    # wrong password
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_parent_token_cannot_access_admin_routes(client):
    _setup_and_login(client)
    from app.security import create_token

    parent_token = create_token("parent:stu_1:grd_1")
    h = {"Authorization": f"Bearer {parent_token}"}

    r = client.get("/api/v1/classes", headers=h)
    assert r.status_code == 403

    r = client.get("/api/v1/integrations/google-drive/status", headers=h)
    assert r.status_code == 403


def test_class_crud(client):
    token = _setup_and_login(client)
    h = {"Authorization": f"Bearer {token}"}
    r = client.post("/api/v1/classes", json={"name": "測試班"}, headers=h)
    assert r.status_code == 200
    cid = r.json()["id"]
    r = client.get("/api/v1/classes", headers=h)
    assert len(r.json()["items"]) == 1
    assert r.json()["items"][0]["name"] == "測試班"
    r = client.delete(f"/api/v1/classes/{cid}", headers=h)
    assert r.status_code == 200


def test_student_limit(client):
    token = _setup_and_login(client)
    h = {"Authorization": f"Bearer {token}"}
    for i in range(5):
        r = client.post(
            "/api/v1/students",
            json={"name": f"學生{i}", "student_code": f"S{i:03d}"},
            headers=h,
        )
        assert r.status_code == 200
    r = client.get("/api/v1/students", headers=h)
    assert len(r.json()["items"]) == 5


def test_post_crud(client):
    token = _setup_and_login(client)
    h = {"Authorization": f"Bearer {token}"}
    cid = client.post("/api/v1/classes", json={"name": "A班"}, headers=h).json()["id"]
    client.post("/api/v1/students", json={"name": "小明"}, headers=h)
    # add membership
    sid = client.get("/api/v1/students", headers=h).json()["items"][0]["id"]
    client.post("/api/v1/memberships", json={"class_id": cid, "student_id": sid}, headers=h)

    r = client.post(
        "/api/v1/posts",
        json={"class_id": cid, "title": "測試公告", "content": "內容", "category": "announcement"},
        headers=h,
    )
    assert r.status_code == 200
    pid = r.json()["id"]

    r = client.get("/api/v1/posts/today", headers=h)
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1
    assert r.json()["items"][0]["title"] == "測試公告"

    r = client.post("/api/v1/posts/delete", json={"ids": [pid]}, headers=h)
    assert r.status_code == 200


def test_post_image_upload_and_fetch_local(client):
    token = _setup_and_login(client)
    h = {"Authorization": f"Bearer {token}"}
    cid = client.post("/api/v1/classes", json={"name": "A班"}, headers=h).json()["id"]
    r = client.post(
        "/api/v1/posts",
        json={"class_id": cid, "title": "有圖片", "content": "內容", "category": "announcement"},
        headers=h,
    )
    assert r.status_code == 200
    pid = r.json()["id"]

    r = client.post(
        f"/api/v1/posts/{pid}/images",
        files={"file": ("sample.png", b"png-bytes", "image/png")},
        headers=h,
    )
    assert r.status_code == 200
    image = r.json()
    assert image["url"] == f"/posts/{pid}/images/{image['id']}"

    r = client.get(f"/api/v1/posts/{pid}/images/{image['id']}")
    assert r.status_code == 200
    assert r.content == b"png-bytes"
    assert r.headers["content-type"] == "image/png"


def test_google_drive_status_and_missing_config(client):
    token = _setup_and_login(client)
    h = {"Authorization": f"Bearer {token}"}

    r = client.get("/api/v1/integrations/google-drive/status", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "disconnected"
    assert r.json()["connected"] is False
    assert r.json()["config"]["configured"] is False
    assert "redirect_uri" in r.json()["config"]
    assert "CLASSHUB_GOOGLE_CLIENT_ID" in r.json()["config"]["missing"]
    assert "change-me" not in str(r.json())
    assert "client-secret" not in str(r.json())

    r = client.get("/api/v1/integrations/google-drive/connect?as_json=true", headers=h)
    assert r.status_code == 503
    assert "CLASSHUB_GOOGLE_CLIENT_ID" in r.json()["detail"]


def test_storage_settings_provider_selection(client):
    token = _setup_and_login(client)
    h = {"Authorization": f"Bearer {token}"}

    r = client.get("/api/v1/storage/settings", headers=h)
    assert r.status_code == 200
    assert r.json()["provider"] == "local"

    r = client.put("/api/v1/storage/settings", json={"provider": "bad"}, headers=h)
    assert r.status_code == 422

    r = client.put("/api/v1/storage/settings", json={"provider": "google_drive"}, headers=h)
    assert r.status_code == 409

    import app.db
    from app.db import _gen_id, _now

    db = app.db.get_db()
    with db.conn() as c:
        c.execute(
            "INSERT INTO external_integrations("
            "id, owner_id, provider, status, config_json, created_at, updated_at"
            ") VALUES(?,?,?,?,?,?,?)",
            (_gen_id("int"), "admin", "google_drive", "connected", '{"folder_id":"folder_1"}', _now(), _now()),
        )

    r = client.put("/api/v1/storage/settings", json={"provider": "google_drive"}, headers=h)
    assert r.status_code == 200
    assert r.json()["provider"] == "google_drive"

    r = client.get("/api/v1/storage/settings", headers=h)
    assert r.status_code == 200
    assert r.json()["provider"] == "google_drive"

    r = client.post("/api/v1/integrations/google-drive/disconnect", headers=h)
    assert r.status_code == 200
    r = client.get("/api/v1/storage/settings", headers=h)
    assert r.status_code == 200
    assert r.json()["provider"] == "local"


def test_upload_reports_google_drive_config_error_when_selected_without_key(client):
    token = _setup_and_login(client)
    h = {"Authorization": f"Bearer {token}"}

    import app.db
    from app.db import _gen_id, _now

    db = app.db.get_db()
    with db.conn() as c:
        c.execute(
            "INSERT INTO external_integrations("
            "id, owner_id, provider, status, config_json, created_at, updated_at"
            ") VALUES(?,?,?,?,?,?,?)",
            (_gen_id("int"), "admin", "google_drive", "connected", '{"folder_id":"folder_1"}', _now(), _now()),
        )
    db.set_setting("image_storage_provider", "google_drive")

    cid = client.post("/api/v1/classes", json={"name": "A班"}, headers=h).json()["id"]
    pid = client.post(
        "/api/v1/posts",
        json={"class_id": cid, "title": "Drive 圖片", "content": "內容", "category": "announcement"},
        headers=h,
    ).json()["id"]

    r = client.post(
        f"/api/v1/posts/{pid}/images",
        files={"file": ("sample.png", b"png-bytes", "image/png")},
        headers=h,
    )
    assert r.status_code == 503
    assert "CLASSHUB_TOKEN_ENCRYPTION_KEY" in r.json()["detail"]


def test_upload_rejects_google_drive_reauth_required(client):
    token = _setup_and_login(client)
    h = {"Authorization": f"Bearer {token}"}

    import app.db
    from app.db import _gen_id, _now

    db = app.db.get_db()
    with db.conn() as c:
        c.execute(
            "INSERT INTO external_integrations("
            "id, owner_id, provider, status, last_error, config_json, created_at, updated_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            (
                _gen_id("int"),
                "admin",
                "google_drive",
                "reauth_required",
                "refresh failed",
                '{"folder_id":"folder_1"}',
                _now(),
                _now(),
            ),
        )
    db.set_setting("image_storage_provider", "google_drive")

    cid = client.post("/api/v1/classes", json={"name": "A班"}, headers=h).json()["id"]
    pid = client.post(
        "/api/v1/posts",
        json={"class_id": cid, "title": "Drive 圖片", "content": "內容", "category": "announcement"},
        headers=h,
    ).json()["id"]

    r = client.post(
        f"/api/v1/posts/{pid}/images",
        files={"file": ("sample.png", b"png-bytes", "image/png")},
        headers=h,
    )
    assert r.status_code == 409
    assert "not connected" in r.json()["detail"]


def test_feedback(client):
    r = client.post("/api/v1/feedback", json={"body": "很好用！"})
    assert r.status_code == 200
