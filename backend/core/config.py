import secrets
import logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TOME_", env_file=".env", extra="ignore")

    secret_key: str | None = None
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

    @property
    def secret_key_file(self) -> Path:
        return self.data_dir / "secret.key"

    def resolve_secret_key(self) -> str:
        """Return the JWT signing key. Called at startup after ensure_dirs().

        Precedence:
          1. TOME_SECRET_KEY env var (if set and non-empty).
          2. Contents of {data_dir}/secret.key.
          3. Generate a new 64-byte key, persist to {data_dir}/secret.key (mode 0600).
        """
        if self.secret_key and self.secret_key.strip():
            if self.secret_key.strip() == "change-me-in-production":
                raise RuntimeError(
                    "TOME_SECRET_KEY is set to the historical default literal "
                    "'change-me-in-production'. Unset it (to auto-generate) or "
                    "set it to a real random value."
                )
            return self.secret_key.strip()

        key_file = self.secret_key_file
        if key_file.exists():
            return key_file.read_text().strip()

        new_key = secrets.token_urlsafe(64)
        key_file.write_text(new_key)
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
        log.warning(
            "TOME_SECRET_KEY not set. Generated a new key at %s. "
            "All pre-existing JWTs are now invalid; users must log in again.",
            key_file,
        )
        return new_key

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.incoming_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
