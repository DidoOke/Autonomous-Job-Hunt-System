from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@db:5432/jobhunt"
    alembic_database_url: str = "postgresql://postgres:postgres@db:5432/jobhunt"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    notify_enabled: bool = True
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
