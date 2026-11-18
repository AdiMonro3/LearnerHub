from pathlib import Path
from typing import Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env, found the same way whichever folder a command runs from (uvicorn,
# pytest, alembic, celery). Settings there override the defaults below. Optional.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

# Lets the app run locally without a .env file. Outside env=dev, startup refuses it.
# 32+ bytes, because HS256 keys shorter than that make PyJWT warn on every token.
DEV_JWT_SECRET = "dev-only-jwt-secret-do-not-use-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8")

    env: str = "dev"
    # The defaults match compose.yaml, so `docker compose up -d` is all the setup needed.
    database_url: str = "postgresql+asyncpg://learnhub:learnhub@localhost:5433/learnhub"
    redis_url: str = "redis://localhost:6379/0"
    rabbitmq_url: str = "amqp://learnhub:learnhub@localhost:5672//"
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "learnhub"

    # Outgoing email. The defaults point at Mailpit from compose.yaml.
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    mail_from: str = "LearnerHub <no-reply@learnerhub.local>"

    # Signs access tokens. SecretStr prints as '**********', so the secret can't leak into
    # logs or error messages. Generate a real one with:
    #   python -c "import secrets; print(secrets.token_urlsafe(32))"
    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    access_token_minutes: int = 15
    refresh_token_days: int = 7

    # Where the React app runs. CORS lets only this origin call the API with cookies,
    # and Google sign-in and email links send the user back here.
    frontend_url: str = "http://localhost:5173"
    # Secure cookies travel over HTTPS only. Off locally, where everything is http://.
    cookie_secure: bool = False

    # Google sign-in stays switched off until both of these are set.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    # Must exactly match an "Authorized redirect URI" of the Google OAuth client.
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    @field_validator("frontend_url")
    @classmethod
    def _no_trailing_slash(cls, url: str) -> str:
        # A browser's Origin header never ends in "/", so CORS would never match one.
        return url.rstrip("/")

    @model_validator(mode="after")
    def _require_real_secret_outside_dev(self) -> Self:
        secret = self.jwt_secret.get_secret_value()
        if self.env != "dev" and (
            secret == DEV_JWT_SECRET or len(secret.encode()) < 32
        ):
            raise ValueError(
                "JWT_SECRET must be at least 32 random bytes outside env=dev"
            )
        return self


settings = Settings()
