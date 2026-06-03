import os
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
    # Canonical public origin (env TOME_PUBLIC_URL), e.g. "https://tome.example.org".
    # When set, it is baked verbatim into the KOReader plugin's SERVER_URL — the
    # authoritative way to pin the scheme/host behind a reverse proxy. When unset,
    # the plugin URL is inferred from the request (honouring X-Forwarded-Proto), so
    # plain HTTP / LAN / localhost deployments need not set it.
    public_url: str | None = None
    # Optional Google Books API key (env TOME_GOOGLE_BOOKS_KEY). When set, Google
    # Books requests are charged against your own Cloud project quota instead of
    # the shared anonymous pool — fixes 429/quota failures, which hit hardest for
    # non-English (e.g. zh-TW) catalogues that lean on Google as the fallback.
    google_books_key: str | None = None

    # SMTP (send-to-device)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_daily_limit: int = 50

    # Auto-import settings
    auto_import: bool = False
    auto_import_interval: int = 300  # seconds

    # Library scan: worker processes for the CPU-bound extract/hash phase.
    # Default 1 = serial, in-process. This is deliberately the lean default:
    # serial already out-ingests every peer while keeping the lowest RAM, which
    # suits the modest hardware Tome usually runs on. Set TOME_SCAN_WORKERS>1
    # (e.g. = CPU cores) to fan extraction out across processes for big imports
    # on boxes with RAM to spare — each worker adds roughly 60-80 MB. DB writes
    # stay single-process regardless (SQLite is single-writer).
    scan_workers: int = 1

    # Wishlist feature
    wishlist_enabled: bool = True   # env TOME_WISHLIST_ENABLED — kill switch, defaults on
    wishlist_max_open_per_user: int = 100  # env TOME_WISHLIST_MAX — soft cap per user

    # JWT settings
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    @property
    def smtp_from_address(self) -> str:
        return self.smtp_from or self.smtp_user or ""

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
