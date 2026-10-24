"""Settings read from the environment (and a .env file in development)."""

import os
from dataclasses import dataclass

from pathlib import Path

from dotenv import load_dotenv

_DEV_SECRET = "dev-secret-key-change-me"


@dataclass(frozen=True)
class Settings:
    env: str
    database_url: str
    secret_key: str
    session_cookie_secure: bool
    session_max_age_seconds: int
    max_body_bytes: int
    # SLIM's reference tables. Defaults to DATABASE_URL: one Postgres shared with the ecosystem.
    slim_database_url: str | None = None
    reference_cache_ttl_seconds: int = 300
    # The lab's local time zone: "today" for receipt and expected-arrival dates.
    lab_timezone: str = "America/Chicago"
    # Bearer token slim-lims uses for the API (spec §12). Unset = no machine access.
    lims_api_token: str | None = None
    # Public sandbox: offer "Continue as..." demo personas, recorded as ASSERTED (slim-data D-50).
    demo_personas: bool = False

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _database_url(raw: str) -> str:
    # Render and Heroku hand out postgres:// or postgresql:// URLs with no driver; SQLAlchemy 2 refuses
    # the first and may pick psycopg 3 for the second. The portal ships psycopg2, so name it.
    for scheme in ("postgres://", "postgresql://"):
        if raw.startswith(scheme):
            return "postgresql+psycopg2://" + raw.removeprefix(scheme)
    return raw


def load_settings() -> Settings:
    # Only ./.env -- never search parent directories, which could pick up another system's settings.
    local_env = Path.cwd() / ".env"
    if local_env.is_file():
        load_dotenv(local_env)
    env = os.environ.get("APP_ENV", "development").strip().lower()
    secret_key = os.environ.get("SECRET_KEY", "")
    if not secret_key:
        if env == "production":
            raise RuntimeError("SECRET_KEY must be set when APP_ENV=production")
        secret_key = _DEV_SECRET
    lims_api_token = os.environ.get("LIMS_API_TOKEN", "").strip() or None
    if lims_api_token is not None and len(lims_api_token) < 32:
        raise RuntimeError("LIMS_API_TOKEN must be at least 32 characters (e.g. python -c "
                           "'import secrets; print(secrets.token_urlsafe(32))')")
    return Settings(
        env=env,
        database_url=_database_url(os.environ.get("DATABASE_URL", "sqlite:///slim_lab_portal.db")),
        secret_key=secret_key,
        session_cookie_secure=_bool(os.environ.get("SESSION_COOKIE_SECURE"), env == "production"),
        session_max_age_seconds=int(os.environ.get("SESSION_MAX_AGE_SECONDS", 8 * 60 * 60)),
        max_body_bytes=int(os.environ.get("MAX_BODY_BYTES", 1024 * 1024)),
        slim_database_url=_database_url(os.environ["SLIM_DATABASE_URL"]) if os.environ.get("SLIM_DATABASE_URL") else None,
        reference_cache_ttl_seconds=int(os.environ.get("REFERENCE_CACHE_TTL_SECONDS", 300)),
        lab_timezone=os.environ.get("LAB_TIMEZONE", "America/Chicago"),
        lims_api_token=lims_api_token,
        demo_personas=_bool(os.environ.get("DEMO_PERSONAS"), False),
    )
