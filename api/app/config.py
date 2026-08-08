"""ClassHub OSS — Configuration"""
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    app_name: str = "ClassHub"
    app_version: str = "0.1.0"
    api_version: str = "v1"
    classhub_id: str = Field(
        default="local-classhub",
        validation_alias=AliasChoices("CLASSHUB_ID", "CLASSHUB_CLASSHUB_ID"),
    )

    # Security
    secret_key: str = Field(
        default="dev-secret-change-me",
        validation_alias=AliasChoices("CLASSHUB_SECRET", "CLASSHUB_SECRET_KEY"),
    )
    access_token_expire_minutes: int = 1440  # 24h

    # Storage
    db_path: str = Field(
        default="classhub.db",
        validation_alias=AliasChoices("CLASSHUB_DB", "CLASSHUB_DB_PATH"),
    )
    image_storage_provider: str = Field(
        default="local",
        validation_alias="CLASSHUB_IMAGE_STORAGE_PROVIDER",
    )

    # Limits
    max_students: int = 150
    max_guardians_per_student: int = 4

    # Upload
    upload_dir: str = Field(default="uploads", validation_alias="CLASSHUB_UPLOAD_DIR")
    max_image_size: int = 5 * 1024 * 1024  # 5MB

    # CORS
    cors_origins: list[str] = ["*"]

    # Integrations
    token_encryption_key: str = Field(
        default="",
        validation_alias="CLASSHUB_TOKEN_ENCRYPTION_KEY",
    )
    google_client_id: str = Field(default="", validation_alias="CLASSHUB_GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", validation_alias="CLASSHUB_GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = Field(default="", validation_alias="CLASSHUB_GOOGLE_REDIRECT_URI")
    google_oauth_authorize_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    google_oauth_token_url: str = "https://oauth2.googleapis.com/token"
    google_oauth_tokeninfo_url: str = "https://oauth2.googleapis.com/tokeninfo"
    google_oauth_revoke_url: str = "https://oauth2.googleapis.com/revoke"
    google_drive_api_base: str = "https://www.googleapis.com"
    google_drive_folder_name: str = "ClassHub"
    google_drive_images_folder_name: str = "Images"

    model_config = {"env_file": ".env", "env_prefix": "CLASSHUB_"}

_settings = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
