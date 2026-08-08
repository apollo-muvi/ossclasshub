"""Google Drive OAuth credentials and image storage adapter."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings
from app.db import Database, _gen_id, _now
from app.security import create_token, verify_token
from app.storage import ImageFile, ImageStorage, RetrievedImage, StorageContext, StoredImage


GOOGLE_DRIVE_PROVIDER = "google_drive"
GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"


class GoogleDriveError(Exception):
    """Base integration error safe to map to an API response."""


class GoogleDriveConfigError(GoogleDriveError):
    """Google Drive integration is not configured."""


class GoogleDriveAuthError(GoogleDriveError):
    """Google Drive credentials are missing or need reauthorization."""


class GoogleDriveApiError(GoogleDriveError):
    """Google Drive API request failed."""


@dataclass
class ExternalIntegration:
    id: str
    owner_id: str
    provider: str
    status: str
    access_token_encrypted: str
    refresh_token_encrypted: str
    token_expires_at: str
    scopes: str
    external_account_id: str
    config: dict[str, Any]
    last_error: str
    created_at: str
    updated_at: str


@dataclass
class GoogleCredentials:
    owner_id: str
    access_token: str
    refresh_token: str
    expires_at: datetime
    scopes: str
    external_account_id: str
    config: dict[str, Any]


class TokenCipher:
    def __init__(self, key: str):
        if not key:
            raise GoogleDriveConfigError("CLASSHUB_TOKEN_ENCRYPTION_KEY is required")
        try:
            self._fernet = Fernet(key.encode())
        except ValueError as exc:
            raise GoogleDriveConfigError("CLASSHUB_TOKEN_ENCRYPTION_KEY must be a Fernet key") from exc

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise GoogleDriveAuthError("stored Google Drive credential cannot be decrypted") from exc


class ExternalIntegrationRepository:
    def __init__(self, db: Database):
        self.db = db

    def get(self, owner_id: str, provider: str) -> ExternalIntegration | None:
        with self.db.conn() as c:
            row = c.execute(
                "SELECT * FROM external_integrations WHERE owner_id=? AND provider=?",
                (owner_id, provider),
            ).fetchone()
        if not row:
            return None
        return ExternalIntegration(
            id=row["id"],
            owner_id=row["owner_id"],
            provider=row["provider"],
            status=row["status"],
            access_token_encrypted=row["access_token_encrypted"] or "",
            refresh_token_encrypted=row["refresh_token_encrypted"] or "",
            token_expires_at=row["token_expires_at"] or "",
            scopes=row["scopes"] or "",
            external_account_id=row["external_account_id"] or "",
            config=json.loads(row["config_json"] or "{}"),
            last_error=row["last_error"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def upsert_connected(
        self,
        owner_id: str,
        provider: str,
        access_token_encrypted: str,
        refresh_token_encrypted: str,
        token_expires_at: str,
        scopes: str,
        external_account_id: str,
        config: dict[str, Any],
    ) -> ExternalIntegration:
        existing = self.get(owner_id, provider)
        now = _now()
        integration_id = existing.id if existing else _gen_id("int")
        created_at = existing.created_at if existing else now
        with self.db.conn() as c:
            c.execute(
                "INSERT INTO external_integrations("
                "id, owner_id, provider, status, access_token_encrypted, refresh_token_encrypted, "
                "token_expires_at, scopes, external_account_id, config_json, last_error, created_at, updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(owner_id, provider) DO UPDATE SET "
                "status=excluded.status, access_token_encrypted=excluded.access_token_encrypted, "
                "refresh_token_encrypted=excluded.refresh_token_encrypted, "
                "token_expires_at=excluded.token_expires_at, scopes=excluded.scopes, "
                "external_account_id=excluded.external_account_id, config_json=excluded.config_json, "
                "last_error='', updated_at=excluded.updated_at",
                (
                    integration_id,
                    owner_id,
                    provider,
                    "connected",
                    access_token_encrypted,
                    refresh_token_encrypted,
                    token_expires_at,
                    scopes,
                    external_account_id,
                    json.dumps(config),
                    "",
                    created_at,
                    now,
                ),
            )
        saved = self.get(owner_id, provider)
        if saved is None:
            raise GoogleDriveApiError("failed to save Google Drive integration")
        return saved

    def update_access_token(self, integration: ExternalIntegration, access_token: str, expires_at: str) -> None:
        with self.db.conn() as c:
            c.execute(
                "UPDATE external_integrations SET access_token_encrypted=?, token_expires_at=?, "
                "status='connected', last_error='', updated_at=? WHERE id=?",
                (access_token, expires_at, _now(), integration.id),
            )

    def update_config(self, integration: ExternalIntegration, config: dict[str, Any]) -> None:
        with self.db.conn() as c:
            c.execute(
                "UPDATE external_integrations SET config_json=?, updated_at=? WHERE id=?",
                (json.dumps(config), _now(), integration.id),
            )

    def set_status(self, integration: ExternalIntegration, status: str, last_error: str = "") -> None:
        with self.db.conn() as c:
            c.execute(
                "UPDATE external_integrations SET status=?, last_error=?, updated_at=? WHERE id=?",
                (status, last_error, _now(), integration.id),
            )

    def disconnect(self, owner_id: str, provider: str) -> None:
        integration = self.get(owner_id, provider)
        if not integration:
            return
        with self.db.conn() as c:
            c.execute(
                "UPDATE external_integrations SET status='disconnected', "
                "access_token_encrypted='', refresh_token_encrypted='', last_error='', updated_at=? "
                "WHERE id=?",
                (_now(), integration.id),
            )


class GoogleDriveRestClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    def _client(self, timeout: int) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.transport)

    async def _request(
        self,
        method: str,
        url: str,
        timeout: int,
        error: GoogleDriveError,
        **kwargs,
    ) -> httpx.Response:
        try:
            async with self._client(timeout=timeout) as client:
                return await client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            raise error from exc

    async def token_request(self, data: dict[str, str]) -> dict[str, Any]:
        response = await self._request(
            "POST",
            self.settings.google_oauth_token_url,
            timeout=20,
            error=GoogleDriveAuthError("Google OAuth token request failed"),
            data=data,
        )
        if response.status_code >= 400:
            raise GoogleDriveAuthError("Google OAuth token request failed")
        return response.json()

    async def token_info(self, access_token: str) -> dict[str, Any]:
        try:
            response = await self._request(
                "GET",
                self.settings.google_oauth_tokeninfo_url,
                timeout=20,
                error=GoogleDriveAuthError("Google OAuth token info request failed"),
                params={"access_token": access_token},
            )
        except GoogleDriveAuthError:
            return {}
        if response.status_code >= 400:
            return {}
        return response.json()

    async def revoke(self, token: str) -> None:
        response = await self._request(
            "POST",
            self.settings.google_oauth_revoke_url,
            timeout=20,
            error=GoogleDriveAuthError("Google OAuth revoke request failed"),
            data={"token": token},
        )
        if response.status_code >= 400:
            raise GoogleDriveAuthError("Google OAuth revoke request failed")

    async def ensure_images_folder(self, access_token: str) -> str:
        classhub_id = await self._ensure_folder(access_token, self.settings.google_drive_folder_name, parent_id=None)
        return await self._ensure_folder(
            access_token,
            self.settings.google_drive_images_folder_name,
            parent_id=classhub_id,
        )

    async def upload(self, access_token: str, folder_id: str, image: ImageFile) -> str:
        metadata = {
            "name": f"{image.image_id}{image.ext}",
            "mimeType": image.mime_type,
            "parents": [folder_id],
        }
        files = {
            "metadata": (None, json.dumps(metadata), "application/json; charset=UTF-8"),
            "file": (metadata["name"], image.data, image.mime_type),
        }
        response = await self._request(
            "POST",
            f"{self.settings.google_drive_api_base}/upload/drive/v3/files",
            timeout=60,
            error=GoogleDriveApiError("Google Drive upload failed"),
            params={"uploadType": "multipart", "fields": "id"},
            headers={"Authorization": f"Bearer {access_token}"},
            files=files,
        )
        if response.status_code >= 400:
            raise GoogleDriveApiError("Google Drive upload failed")
        file_id = response.json().get("id")
        if not file_id:
            raise GoogleDriveApiError("Google Drive upload returned no file id")
        return file_id

    async def download(self, access_token: str, file_id: str) -> bytes:
        response = await self._request(
            "GET",
            f"{self.settings.google_drive_api_base}/drive/v3/files/{file_id}",
            timeout=60,
            error=GoogleDriveApiError("Google Drive download failed"),
            params={"alt": "media"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code == 404:
            raise FileNotFoundError(file_id)
        if response.status_code >= 400:
            raise GoogleDriveApiError("Google Drive download failed")
        return response.content

    async def delete(self, access_token: str, file_id: str) -> None:
        response = await self._request(
            "DELETE",
            f"{self.settings.google_drive_api_base}/drive/v3/files/{file_id}",
            timeout=30,
            error=GoogleDriveApiError("Google Drive delete failed"),
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code in (204, 404):
            return
        if response.status_code >= 400:
            raise GoogleDriveApiError("Google Drive delete failed")

    async def exists(self, access_token: str, file_id: str) -> bool:
        response = await self._request(
            "GET",
            f"{self.settings.google_drive_api_base}/drive/v3/files/{file_id}",
            timeout=30,
            error=GoogleDriveApiError("Google Drive exists check failed"),
            params={"fields": "id"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code == 404:
            return False
        if response.status_code >= 400:
            raise GoogleDriveApiError("Google Drive exists check failed")
        return True

    async def _ensure_folder(self, access_token: str, name: str, parent_id: str | None) -> str:
        folder_id = await self._find_folder(access_token, name, parent_id)
        if folder_id:
            return folder_id
        metadata: dict[str, Any] = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            metadata["parents"] = [parent_id]
        response = await self._request(
            "POST",
            f"{self.settings.google_drive_api_base}/drive/v3/files",
            timeout=30,
            error=GoogleDriveApiError("Google Drive folder creation failed"),
            params={"fields": "id"},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=metadata,
        )
        if response.status_code >= 400:
            raise GoogleDriveApiError("Google Drive folder creation failed")
        folder_id = response.json().get("id")
        if not folder_id:
            raise GoogleDriveApiError("Google Drive folder creation returned no id")
        return folder_id

    async def _find_folder(self, access_token: str, name: str, parent_id: str | None) -> str | None:
        escaped_name = name.replace("'", "\\'")
        query = (
            "mimeType='application/vnd.google-apps.folder' "
            f"and name='{escaped_name}' and trashed=false"
        )
        if parent_id:
            query += f" and '{parent_id}' in parents"
        response = await self._request(
            "GET",
            f"{self.settings.google_drive_api_base}/drive/v3/files",
            timeout=30,
            error=GoogleDriveApiError("Google Drive folder lookup failed"),
            params={"q": query, "fields": "files(id)", "pageSize": "1"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code >= 400:
            raise GoogleDriveApiError("Google Drive folder lookup failed")
        files = response.json().get("files") or []
        return files[0].get("id") if files else None


class GoogleCredentialProvider:
    def __init__(self, settings: Settings, repository: ExternalIntegrationRepository, drive: GoogleDriveRestClient):
        self.settings = settings
        self.repository = repository
        self.drive = drive

    def require_oauth_config(self) -> None:
        missing = self.missing_config()
        if missing:
            raise GoogleDriveConfigError(f"missing Google OAuth config: {', '.join(missing)}")
        TokenCipher(self.settings.token_encryption_key)

    def missing_config(self) -> list[str]:
        return [
            name for name, value in (
                ("CLASSHUB_GOOGLE_CLIENT_ID", self.settings.google_client_id),
                ("CLASSHUB_GOOGLE_CLIENT_SECRET", self.settings.google_client_secret),
                ("CLASSHUB_GOOGLE_REDIRECT_URI", self.settings.google_redirect_uri),
                ("CLASSHUB_TOKEN_ENCRYPTION_KEY", self.settings.token_encryption_key),
            )
            if not value
        ]

    def config_status(self) -> dict[str, Any]:
        missing = self.missing_config()
        if not missing and self.settings.token_encryption_key:
            try:
                TokenCipher(self.settings.token_encryption_key)
            except GoogleDriveConfigError:
                missing = ["CLASSHUB_TOKEN_ENCRYPTION_KEY"]
        return {
            "configured": len(missing) == 0,
            "missing": missing,
            "scope": GOOGLE_DRIVE_SCOPE,
            "redirect_uri": self.settings.google_redirect_uri,
        }

    def build_authorization_url(self, owner_id: str) -> str:
        self.require_oauth_config()
        state = create_token(f"{GOOGLE_DRIVE_PROVIDER}:{owner_id}")
        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.google_redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_DRIVE_SCOPE,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        }
        return f"{self.settings.google_oauth_authorize_url}?{urlencode(params)}"

    def owner_from_state(self, state: str) -> str:
        payload = verify_token(state)
        if not payload or not payload.startswith(f"{GOOGLE_DRIVE_PROVIDER}:"):
            raise GoogleDriveAuthError("invalid Google Drive OAuth state")
        return payload.split(":", 1)[1]

    async def connect_with_code(self, owner_id: str, code: str) -> ExternalIntegration:
        self.require_oauth_config()
        cipher = TokenCipher(self.settings.token_encryption_key)
        token = await self.drive.token_request(
            {
                "code": code,
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "redirect_uri": self.settings.google_redirect_uri,
                "grant_type": "authorization_code",
            }
        )
        access_token = token.get("access_token") or ""
        refresh_token = token.get("refresh_token") or ""
        if not access_token or not refresh_token:
            raise GoogleDriveAuthError("Google OAuth did not return both access and refresh tokens")
        expires_at = self._expires_at(token)
        token_info = await self.drive.token_info(access_token)
        scopes = token.get("scope") or token_info.get("scope") or GOOGLE_DRIVE_SCOPE
        external_account_id = token_info.get("sub") or token_info.get("user_id") or token_info.get("email") or ""
        folder_id = await self.drive.ensure_images_folder(access_token)
        return self.repository.upsert_connected(
            owner_id=owner_id,
            provider=GOOGLE_DRIVE_PROVIDER,
            access_token_encrypted=cipher.encrypt(access_token),
            refresh_token_encrypted=cipher.encrypt(refresh_token),
            token_expires_at=expires_at.isoformat(),
            scopes=scopes,
            external_account_id=external_account_id,
            config={"folder_id": folder_id},
        )

    async def get_credentials(self, owner_id: str) -> GoogleCredentials:
        integration = self.repository.get(owner_id, GOOGLE_DRIVE_PROVIDER)
        if not integration or integration.status != "connected":
            raise GoogleDriveAuthError("Google Drive is not connected for this owner")
        cipher = TokenCipher(self.settings.token_encryption_key)
        refresh_token = cipher.decrypt(integration.refresh_token_encrypted)
        access_token = cipher.decrypt(integration.access_token_encrypted)
        expires_at = self._parse_expires_at(integration.token_expires_at)
        if expires_at <= datetime.now(timezone.utc) + timedelta(minutes=2):
            access_token, expires_at = await self._refresh(integration, refresh_token, cipher)
        return GoogleCredentials(
            owner_id=owner_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scopes=integration.scopes,
            external_account_id=integration.external_account_id,
            config=integration.config,
        )

    async def ensure_folder_id(self, credentials: GoogleCredentials) -> str:
        folder_id = credentials.config.get("folder_id")
        if folder_id:
            return folder_id
        folder_id = await self.drive.ensure_images_folder(credentials.access_token)
        integration = self.repository.get(credentials.owner_id, GOOGLE_DRIVE_PROVIDER)
        if integration:
            config = dict(credentials.config)
            config["folder_id"] = folder_id
            self.repository.update_config(integration, config)
            credentials.config = config
        return folder_id

    async def disconnect(self, owner_id: str, revoke: bool = False) -> bool:
        integration = self.repository.get(owner_id, GOOGLE_DRIVE_PROVIDER)
        revoked = False
        if revoke and integration and integration.status == "connected":
            try:
                cipher = TokenCipher(self.settings.token_encryption_key)
                token = cipher.decrypt(integration.access_token_encrypted or integration.refresh_token_encrypted)
                await self.drive.revoke(token)
                revoked = True
            except GoogleDriveError:
                revoked = False
        self.repository.disconnect(owner_id, GOOGLE_DRIVE_PROVIDER)
        return revoked

    async def _refresh(
        self,
        integration: ExternalIntegration,
        refresh_token: str,
        cipher: TokenCipher,
    ) -> tuple[str, datetime]:
        try:
            self.require_oauth_config()
            token = await self.drive.token_request(
                {
                    "client_id": self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                }
            )
            access_token = token.get("access_token") or ""
            if not access_token:
                raise GoogleDriveAuthError("Google OAuth refresh returned no access token")
            expires_at = self._expires_at(token)
            self.repository.update_access_token(
                integration,
                cipher.encrypt(access_token),
                expires_at.isoformat(),
            )
            return access_token, expires_at
        except GoogleDriveError as exc:
            self.repository.set_status(integration, "reauth_required", str(exc))
            raise

    def _expires_at(self, token: dict[str, Any]) -> datetime:
        expires_in = int(token.get("expires_in") or 3600)
        return datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    def _parse_expires_at(self, value: str) -> datetime:
        if not value:
            return datetime.fromtimestamp(0, timezone.utc)
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class GoogleDriveImageStorage(ImageStorage):
    provider = GOOGLE_DRIVE_PROVIDER

    def __init__(self, credentials: GoogleCredentialProvider, drive: GoogleDriveRestClient):
        self.credentials = credentials
        self.drive = drive

    async def save(self, context: StorageContext, image: ImageFile) -> StoredImage:
        credentials = await self.credentials.get_credentials(context.owner_id)
        folder_id = await self.credentials.ensure_folder_id(credentials)
        file_id = await self.drive.upload(credentials.access_token, folder_id, image)
        return StoredImage(
            provider=self.provider,
            storage_key=file_id,
            mime_type=image.mime_type,
            size=len(image.data),
        )

    async def open(self, context: StorageContext, storage_key: str) -> RetrievedImage:
        credentials = await self.credentials.get_credentials(context.owner_id)
        data = await self.drive.download(credentials.access_token, storage_key)
        return RetrievedImage(stream=BytesIO(data), mime_type="", size=len(data))

    async def delete(self, context: StorageContext, storage_key: str) -> None:
        credentials = await self.credentials.get_credentials(context.owner_id)
        await self.drive.delete(credentials.access_token, storage_key)

    async def exists(self, context: StorageContext, storage_key: str) -> bool:
        credentials = await self.credentials.get_credentials(context.owner_id)
        return await self.drive.exists(credentials.access_token, storage_key)


def create_google_credential_provider(settings: Settings, db: Database) -> GoogleCredentialProvider:
    drive = GoogleDriveRestClient(settings)
    repository = ExternalIntegrationRepository(db)
    return GoogleCredentialProvider(settings, repository, drive)
