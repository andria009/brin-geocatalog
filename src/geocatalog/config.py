from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    db_host: str = "localhost"
    db_port: int = 55432
    db_name: str = "geocatalog"
    db_user: str = "geocatalog"
    db_password: str = "geocatalog_dev"

    model_config = SettingsConfigDict(env_prefix="GEOCATALOG_", env_file=".env", extra="ignore")

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

