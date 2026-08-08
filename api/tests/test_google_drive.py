"""Unit tests for Google Drive integration internals."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
from cryptography.fernet import Fernet

from app.config import Settings
from app.db import Database
from app.google_drive import (
    GOOGLE_DRIVE_PROVIDER,
    ExternalIntegrationRepository,
    GoogleCredentialProvider,
    GoogleCredentials,
    GoogleDriveApiError,
    GoogleDriveAuthError,
    GoogleDriveImageStorage,
    GoogleDriveRestClient,
    TokenCipher,
)
from app.storage import ImageFile, StorageContext


def _settings():
    return Settings(
        CLASSHUB_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        CLASSHUB_GOOGLE_CLIENT_ID="client-id",
        CLASSHUB_GOOGLE_CLIENT_SECRET="client-secret",
        CLASSHUB_GOOGLE_REDIRECT_URI="http://testserver/api/v1/integrations/google-drive/callback",
    )


def _repo(tmp_path):
    db = Database(str(tmp_path / "classhub.db"))
    return db, ExternalIntegrationRepository(db)


class FakeDrive:
    def __init__(self):
        self.token_requests = []
        self.uploads = []
        self.revoked = []
        self.folder_id = "folder_1"

    async def token_request(self, data):
        self.token_requests.append(data)
        if data["grant_type"] == "authorization_code":
            return {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "expires_in": 3600,
                "scope": "https://www.googleapis.com/auth/drive.file",
            }
        return {"access_token": "refreshed-token", "expires_in": 3600}

    async def token_info(self, access_token):
        return {"sub": "google-account-1"}

    async def ensure_images_folder(self, access_token):
        return self.folder_id

    async def upload(self, access_token, folder_id, image):
        self.uploads.append((access_token, folder_id, image))
        return "drive_file_1"

    async def revoke(self, token):
        self.revoked.append(token)


class FailingRefreshDrive(FakeDrive):
    async def token_request(self, data):
        self.token_requests.append(data)
        if data["grant_type"] == "refresh_token":
            raise GoogleDriveAuthError("refresh failed")
        return await super().token_request(data)


class FailingRevokeDrive(FakeDrive):
    async def revoke(self, token):
        self.revoked.append(token)
        raise GoogleDriveAuthError("revoke failed")


class FakeCredentials:
    async def get_credentials(self, owner_id):
        return GoogleCredentials(
            owner_id=owner_id,
            access_token=f"token-for-{owner_id}",
            refresh_token="refresh-token",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes="",
            external_account_id="",
            config={"folder_id": "folder_1"},
        )

    async def ensure_folder_id(self, credentials):
        return credentials.config["folder_id"]


class FakeStorageDrive:
    def __init__(self):
        self.uploads = []
        self.deleted = []

    async def ensure_images_folder(self, access_token):
        return "folder_1"

    async def upload(self, access_token, folder_id, image):
        self.uploads.append((access_token, folder_id, image))
        return "drive_file_1"

    async def download(self, access_token, file_id):
        return b"drive-bytes"

    async def delete(self, access_token, file_id):
        self.deleted.append((access_token, file_id))

    async def exists(self, access_token, file_id):
        return file_id == "drive_file_1"


def test_authorization_url_round_trips_signed_owner_state(tmp_path):
    settings = _settings()
    db = Database(str(tmp_path / "state.db"))
    provider = GoogleCredentialProvider(settings, ExternalIntegrationRepository(db), FakeDrive())

    url = provider.build_authorization_url("admin")
    params = parse_qs(urlparse(url).query)

    assert params["client_id"] == ["client-id"]
    assert params["scope"] == ["https://www.googleapis.com/auth/drive.file"]
    assert params["access_type"] == ["offline"]
    assert provider.owner_from_state(params["state"][0]) == "admin"


def test_google_config_status_reports_safe_readiness(tmp_path):
    settings = _settings()
    db = Database(str(tmp_path / "state.db"))
    provider = GoogleCredentialProvider(settings, ExternalIntegrationRepository(db), FakeDrive())

    assert provider.config_status() == {
        "configured": True,
        "missing": [],
        "scope": "https://www.googleapis.com/auth/drive.file",
        "redirect_uri": settings.google_redirect_uri,
    }

    bad_settings = Settings(
        CLASSHUB_TOKEN_ENCRYPTION_KEY="not-a-fernet-key",
        CLASSHUB_GOOGLE_CLIENT_ID="client-id",
        CLASSHUB_GOOGLE_CLIENT_SECRET="client-secret",
        CLASSHUB_GOOGLE_REDIRECT_URI="http://testserver/callback",
    )
    bad_provider = GoogleCredentialProvider(bad_settings, ExternalIntegrationRepository(db), FakeDrive())

    assert bad_provider.config_status() == {
        "configured": False,
        "missing": ["CLASSHUB_TOKEN_ENCRYPTION_KEY"],
        "scope": "https://www.googleapis.com/auth/drive.file",
        "redirect_uri": bad_settings.google_redirect_uri,
    }


def test_connect_with_code_encrypts_tokens_and_saves_folder(tmp_path):
    settings = _settings()
    db, repo = _repo(tmp_path)
    drive = FakeDrive()
    provider = GoogleCredentialProvider(settings, repo, drive)

    integration = asyncio.run(provider.connect_with_code("admin", "oauth-code"))

    assert integration.status == "connected"
    assert integration.config["folder_id"] == "folder_1"
    assert integration.external_account_id == "google-account-1"
    assert "access-token" not in integration.access_token_encrypted
    assert "refresh-token" not in integration.refresh_token_encrypted

    credentials = asyncio.run(provider.get_credentials("admin"))
    assert credentials.access_token == "access-token"
    assert credentials.refresh_token == "refresh-token"


def test_expired_access_token_refreshes_and_reencrypts(tmp_path):
    settings = _settings()
    db, repo = _repo(tmp_path)
    cipher = TokenCipher(settings.token_encryption_key)
    repo.upsert_connected(
        owner_id="admin",
        provider=GOOGLE_DRIVE_PROVIDER,
        access_token_encrypted=cipher.encrypt("old-access-token"),
        refresh_token_encrypted=cipher.encrypt("refresh-token"),
        token_expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        scopes="",
        external_account_id="google-account-1",
        config={"folder_id": "folder_1"},
    )
    drive = FakeDrive()
    provider = GoogleCredentialProvider(settings, repo, drive)

    credentials = asyncio.run(provider.get_credentials("admin"))

    assert credentials.access_token == "refreshed-token"
    assert drive.token_requests[-1]["grant_type"] == "refresh_token"
    integration = repo.get("admin", GOOGLE_DRIVE_PROVIDER)
    assert integration.status == "connected"
    assert "refreshed-token" not in integration.access_token_encrypted


def test_refresh_failure_marks_reauth_required(tmp_path):
    settings = _settings()
    db, repo = _repo(tmp_path)
    cipher = TokenCipher(settings.token_encryption_key)
    repo.upsert_connected(
        owner_id="admin",
        provider=GOOGLE_DRIVE_PROVIDER,
        access_token_encrypted=cipher.encrypt("old-access-token"),
        refresh_token_encrypted=cipher.encrypt("refresh-token"),
        token_expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        scopes="",
        external_account_id="google-account-1",
        config={"folder_id": "folder_1"},
    )
    provider = GoogleCredentialProvider(settings, repo, FailingRefreshDrive())

    try:
        asyncio.run(provider.get_credentials("admin"))
    except GoogleDriveAuthError:
        pass
    else:
        raise AssertionError("expected refresh failure")

    integration = repo.get("admin", GOOGLE_DRIVE_PROVIDER)
    assert integration.status == "reauth_required"
    assert integration.last_error == "refresh failed"


def test_disconnect_can_revoke_google_token_before_clearing_credentials(tmp_path):
    settings = _settings()
    db, repo = _repo(tmp_path)
    cipher = TokenCipher(settings.token_encryption_key)
    repo.upsert_connected(
        owner_id="admin",
        provider=GOOGLE_DRIVE_PROVIDER,
        access_token_encrypted=cipher.encrypt("access-token"),
        refresh_token_encrypted=cipher.encrypt("refresh-token"),
        token_expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        scopes="",
        external_account_id="google-account-1",
        config={"folder_id": "folder_1"},
    )
    drive = FakeDrive()
    provider = GoogleCredentialProvider(settings, repo, drive)

    revoked = asyncio.run(provider.disconnect("admin", revoke=True))

    integration = repo.get("admin", GOOGLE_DRIVE_PROVIDER)
    assert revoked is True
    assert drive.revoked == ["access-token"]
    assert integration.status == "disconnected"
    assert integration.access_token_encrypted == ""
    assert integration.refresh_token_encrypted == ""


def test_disconnect_clears_local_credentials_when_revoke_fails(tmp_path):
    settings = _settings()
    db, repo = _repo(tmp_path)
    cipher = TokenCipher(settings.token_encryption_key)
    repo.upsert_connected(
        owner_id="admin",
        provider=GOOGLE_DRIVE_PROVIDER,
        access_token_encrypted=cipher.encrypt("access-token"),
        refresh_token_encrypted=cipher.encrypt("refresh-token"),
        token_expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        scopes="",
        external_account_id="google-account-1",
        config={"folder_id": "folder_1"},
    )
    drive = FailingRevokeDrive()
    provider = GoogleCredentialProvider(settings, repo, drive)

    revoked = asyncio.run(provider.disconnect("admin", revoke=True))

    integration = repo.get("admin", GOOGLE_DRIVE_PROVIDER)
    assert revoked is False
    assert drive.revoked == ["access-token"]
    assert integration.status == "disconnected"
    assert integration.access_token_encrypted == ""
    assert integration.refresh_token_encrypted == ""


def test_google_drive_image_storage_uses_owner_credentials():
    drive = FakeStorageDrive()
    storage = GoogleDriveImageStorage(FakeCredentials(), drive)
    context = StorageContext(owner_id="admin")
    image = ImageFile(
        image_id="img_1",
        data=b"image-bytes",
        mime_type="image/png",
        ext=".png",
    )

    saved = asyncio.run(storage.save(context, image))
    opened = asyncio.run(storage.open(context, saved.storage_key))
    exists = asyncio.run(storage.exists(context, saved.storage_key))
    asyncio.run(storage.delete(context, saved.storage_key))

    assert saved.provider == "google_drive"
    assert saved.storage_key == "drive_file_1"
    assert opened.stream.read() == b"drive-bytes"
    assert exists is True
    assert drive.uploads[0][0] == "token-for-admin"
    assert drive.deleted == [("token-for-admin", "drive_file_1")]


def test_google_drive_image_storage_repairs_missing_folder_id(tmp_path):
    settings = _settings()
    db, repo = _repo(tmp_path)
    cipher = TokenCipher(settings.token_encryption_key)
    repo.upsert_connected(
        owner_id="admin",
        provider=GOOGLE_DRIVE_PROVIDER,
        access_token_encrypted=cipher.encrypt("access-token"),
        refresh_token_encrypted=cipher.encrypt("refresh-token"),
        token_expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        scopes="",
        external_account_id="google-account-1",
        config={},
    )
    drive = FakeDrive()
    provider = GoogleCredentialProvider(settings, repo, drive)
    storage = GoogleDriveImageStorage(provider, drive)
    image = ImageFile("img_1", b"image-bytes", "image/png", ".png")

    saved = asyncio.run(storage.save(StorageContext(owner_id="admin"), image))

    integration = repo.get("admin", GOOGLE_DRIVE_PROVIDER)
    assert saved.storage_key == "drive_file_1"
    assert integration.config["folder_id"] == "folder_1"
    assert drive.uploads == [("access-token", "folder_1", image)]


def test_rest_client_token_request_uses_oauth_endpoint():
    settings = _settings()
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url == settings.google_oauth_token_url
        assert b"grant_type=authorization_code" in request.content
        return httpx.Response(200, json={"access_token": "access-token"})

    client = GoogleDriveRestClient(settings, httpx.MockTransport(handler))

    token = asyncio.run(client.token_request({"grant_type": "authorization_code"}))

    assert token == {"access_token": "access-token"}
    assert len(requests) == 1


def test_rest_client_token_request_maps_transport_error():
    settings = _settings()

    def handler(request):
        raise httpx.ConnectError("cannot connect", request=request)

    client = GoogleDriveRestClient(settings, httpx.MockTransport(handler))

    try:
        asyncio.run(client.token_request({"grant_type": "authorization_code"}))
    except GoogleDriveAuthError as exc:
        assert str(exc) == "Google OAuth token request failed"
    else:
        raise AssertionError("expected OAuth transport error")


def test_rest_client_revoke_uses_oauth_revoke_endpoint():
    settings = _settings()
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url == settings.google_oauth_revoke_url
        assert b"token=access-token" in request.content
        return httpx.Response(200)

    client = GoogleDriveRestClient(settings, httpx.MockTransport(handler))

    asyncio.run(client.revoke("access-token"))

    assert len(requests) == 1


def test_rest_client_ensures_nested_images_folder():
    settings = _settings()
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, dict(request.url.params), request.content))
        assert request.headers["authorization"] == "Bearer access-token"
        if request.method == "GET":
            return httpx.Response(200, json={"files": []})
        metadata = json.loads(request.content.decode())
        if metadata["name"] == "ClassHub":
            return httpx.Response(200, json={"id": "classhub_folder"})
        if metadata["name"] == "Images":
            assert metadata["parents"] == ["classhub_folder"]
            return httpx.Response(200, json={"id": "images_folder"})
        return httpx.Response(500)

    client = GoogleDriveRestClient(settings, httpx.MockTransport(handler))

    folder_id = asyncio.run(client.ensure_images_folder("access-token"))

    assert folder_id == "images_folder"
    assert [call[0] for call in calls] == ["GET", "POST", "GET", "POST"]


def test_rest_client_upload_maps_transport_error():
    settings = _settings()

    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    client = GoogleDriveRestClient(settings, httpx.MockTransport(handler))
    image = ImageFile("img_1", b"image-bytes", "image/png", ".png")

    try:
        asyncio.run(client.upload("access-token", "folder_1", image))
    except GoogleDriveApiError as exc:
        assert str(exc) == "Google Drive upload failed"
    else:
        raise AssertionError("expected Drive upload transport error")


def test_rest_client_file_upload_download_delete_and_exists():
    settings = _settings()
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, dict(request.url.params), request.content))
        assert request.headers["authorization"] == "Bearer access-token"
        if request.method == "POST" and request.url.path == "/upload/drive/v3/files":
            assert request.url.params["uploadType"] == "multipart"
            assert b"image-bytes" in request.content
            return httpx.Response(200, json={"id": "drive_file_1"})
        if request.method == "GET" and request.url.params.get("alt") == "media":
            return httpx.Response(200, content=b"downloaded-bytes")
        if request.method == "GET":
            return httpx.Response(200, json={"id": "drive_file_1"})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(500)

    client = GoogleDriveRestClient(settings, httpx.MockTransport(handler))
    image = ImageFile("img_1", b"image-bytes", "image/png", ".png")

    file_id = asyncio.run(client.upload("access-token", "folder_1", image))
    data = asyncio.run(client.download("access-token", file_id))
    exists = asyncio.run(client.exists("access-token", file_id))
    asyncio.run(client.delete("access-token", file_id))

    assert file_id == "drive_file_1"
    assert data == b"downloaded-bytes"
    assert exists is True
    assert [call[0] for call in calls] == ["POST", "GET", "GET", "DELETE"]
