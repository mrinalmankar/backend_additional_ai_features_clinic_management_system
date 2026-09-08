from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",
        extra="ignore",
        populate_by_name=True,
    )

    environment: str = "development"

    database_url_raw: str = Field(
        default="",
        validation_alias="DATABASE_URL",
    )

    @property
    def database_url(self) -> str:
        if self.database_url_raw:
            return self.database_url_raw
        return "postgresql+asyncpg://clinic_user:clinic_pass@localhost:5432/clinic_db"

    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    llm_provider: str = "groq"

    allow_third_party_llm_for_phi: bool = False

    prescription_storage_dir: str = "storage/prescriptions"
    max_upload_size_bytes: int = 10 * 1024 * 1024

    cors_allowed_origins_raw: str = Field(
        default="",
        validation_alias="CORS_ALLOWED_ORIGINS",
    )

    @property
    def cors_allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins_raw.split(",") if o.strip()]

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/gbp/auth/callback"

    gbp_mock_mode: bool = True

    secret_key: str = ""

    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60

    def validate_for_production(self) -> None:
        if self.environment != "production":
            return

        problems = []
        if not self.database_url_raw:
            problems.append("DATABASE_URL is not set")
        if not self.secret_key or len(self.secret_key) < 32:
            problems.append("SECRET_KEY is not set (or is under 32 chars)")
        if not self.jwt_secret_key or len(self.jwt_secret_key) < 32:
            problems.append("JWT_SECRET_KEY is not set (or is under 32 chars)")
        if self.jwt_secret_key and self.jwt_secret_key == self.secret_key:
            problems.append("JWT_SECRET_KEY must not be the same value as SECRET_KEY")
        if self.gbp_mock_mode:
            problems.append("GBP_MOCK_MODE is still True")
        if not self.cors_allowed_origins:
            problems.append("CORS_ALLOWED_ORIGINS is empty -- no frontend origin is allowed to call this API")
        if not self.google_client_id or not self.google_client_secret:
            problems.append("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set (required once GBP_MOCK_MODE=False)")

        if problems:
            raise RuntimeError(
                "Refusing to start with ENVIRONMENT=production while config is "
                "insecure or incomplete:\n- " + "\n- ".join(problems)
            )

settings = Settings()
