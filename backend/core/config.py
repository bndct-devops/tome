from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TOME_", env_file=".env", extra="ignore")

    secret_key: str = "change-me-in-production"
    data_dir: Path = Path("./data")
    # library_dir: Tome owns this, files are organized here
    library_dir: Path = Path("./library")
    # incoming_dir: the Bindery — files here await triage before entering the library
    incoming_dir: Path = Path("./bindery")
    port: int = 8080
    hardcover_token: str | None = None

    # Auto-import settings
    auto_import: bool = False
    auto_import_interval: int = 300  # seconds

    # JWT settings
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tome.db"

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.incoming_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
