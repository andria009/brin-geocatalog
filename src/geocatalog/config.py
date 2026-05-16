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
    # Must be the publicly reachable nginx/frontend address because large
    # downloads are fulfilled with X-Accel-Redirect.
    api_base_url: str = "http://localhost:8090"

    # Comma-separated mounted roots from which the API is allowed to stream assets.
    asset_roots: str = "/data/geomimo"
    download_ticket_secret: str = "geocatalog-dev-download-ticket-secret"
    download_ticket_ttl_seconds: int = 300
    access_session_timeout_days: int = 3

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

    @property
    def asset_root_paths(self) -> list[str]:
        return [root.strip() for root in self.asset_roots.split(",") if root.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
