from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
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


settings = Settings()
