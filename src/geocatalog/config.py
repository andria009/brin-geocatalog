from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    db_host: str = "localhost"
    db_port: int = 55432
    db_name: str = "geocatalog"
    db_user: str = "geocatalog"
    db_password: str = "geocatalog_dev"

    # PgSTAC database (separate instance used by stac-fastapi-pgstac and the sync worker)
    pgstac_host: str = "localhost"
    pgstac_port: int = 55433
    pgstac_name: str = "pgstac"
    pgstac_user: str = "pgstac"
    pgstac_password: str = "pgstac_dev"

    # Base URL that the stac sync worker will embed in STAC asset hrefs.
    # Must be the publicly reachable address of the geocatalog API.
    api_base_url: str = "http://localhost:8010"

    model_config = SettingsConfigDict(env_prefix="GEOCATALOG_", env_file=".env", extra="ignore")

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def pgstac_dsn(self) -> str:
        return (
            f"postgresql://{self.pgstac_user}:{self.pgstac_password}"
            f"@{self.pgstac_host}:{self.pgstac_port}/{self.pgstac_name}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
