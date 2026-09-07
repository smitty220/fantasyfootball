from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(
        # secrets.env is the visible-in-Finder name the owner edits; .env still
        # works as a conventional fallback. Later files win on conflicts.
        env_file=(".env", "secrets.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    YAHOO_CLIENT_ID: str = ""
    YAHOO_CLIENT_SECRET: str = ""
    # Must exactly match the redirect URI registered on the Yahoo app. Yahoo
    # removed the out-of-band ("oob") flow for apps created after ~Oct 2025,
    # so new apps must register a real https URI.
    YAHOO_REDIRECT_URI: str = "https://localhost:8000/api/auth/yahoo/callback"
    FANTASYPROS_API_KEY: str = ""

    DATABASE_URL: str = "sqlite:///../data/app.db"

    # --- app password gate (see app.services.session_auth) -----------------
    # Leaving OWNER_PASSWORD empty DISABLES the gate entirely: every endpoint
    # is open, which is what local development and the test suite rely on.
    OWNER_PASSWORD: str = ""
    # Read-only guests. Only meaningful while OWNER_PASSWORD is set.
    LEAGUE_PASSWORD: str = ""
    # HMAC key for the signed session cookie. When empty a random one is
    # generated per process, so sessions simply don't survive a restart.
    SESSION_SECRET: str = ""

    # Background auto-refresh of data sources (see app.services.scheduler).
    # Tests force this to False (see tests/conftest.py) so the suite never
    # spins up real APScheduler jobs.
    SCHEDULER_ENABLED: bool = True


settings = Settings()
