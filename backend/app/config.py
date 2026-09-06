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
    FANTASYPROS_API_KEY: str = ""

    DATABASE_URL: str = "sqlite:///../data/app.db"


settings = Settings()
