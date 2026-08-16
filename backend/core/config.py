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

    # Outbound notifications (env TOME_OUTBOUND_NOTIFY): fan in-app
    # notifications out to each user's configured ntfy/Gotify/webhook
    # channels. Off = the kill-switch; per-user channels also toggle.
    outbound_notify: bool = True

    # Update awareness (env TOME_UPDATE_CHECK): lets admins see "new version
    # available" in Settings via a daily-cached GitHub releases lookup. Set
    # false to never call out (air-gapped installs); nothing else phones home.
    update_check: bool = True

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

    # Release detection (follow a series → "vol N is out" alerts). Off by
    # default: it polls Hardcover on a schedule and needs TOME_HARDCOVER_TOKEN.
    release_detection: bool = False        # env TOME_RELEASE_DETECTION
    release_check_interval: int = 86400    # env TOME_RELEASE_CHECK_INTERVAL, seconds

    # Hardcover sync (one-way Tome → Hardcover, ratings + reading progress).
    # Kill switch only: the feature is inert until a user links their PERSONAL
    # Hardcover API token in Settings (TOME_HARDCOVER_TOKEN above stays
    # metadata-fetch-only and is never used for sync).
    hardcover_sync_enabled: bool = True    # env TOME_HARDCOVER_SYNC_ENABLED
    hardcover_sync_interval: int = 1800    # env TOME_HARDCOVER_SYNC_INTERVAL, seconds between reconcile cycles

    # Send-to-KOReader inbox (env TOME_SEND_TO_KOREADER). On by default since
    # v1.7.0 ("Signature") after real-hardware validation; the web UI queues a
    # book to be pulled onto KOReader by the TomeSync plugin's inbox (no
    # email/Amazon). Set to false to disable — the flag remains the kill switch.
    send_to_koreader: bool = True

    # KOSync → plugin position bridge (env TOME_KOSYNC_POSITION_BRIDGE).
    # Experimental, off by default. The KOSync endpoint is deliberately a
    # one-way bridge (issue #156): third-party pushes never write
    # TomeSyncPosition. With this flag on, the PLUGIN's position pull
    # additionally considers the newest KOSync push for the same book —
    # read-side only, TomeSyncPosition is still never written from KOSync
    # data — so a device running the Tome plugin picks up progress made on a
    # third-party KOSync client (Crosspoint, Readest, stock KOReader sync).
    # See issue #175.
    kosync_position_bridge: bool = False

    # JWT settings
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days

    # ── OIDC / SSO (env TOME_OIDC_*) ──────────────────────────────────────────
    # Additive single sign-on. Local username/password login always stays. The
    # callback mints the SAME Tome JWT, so all downstream auth is unchanged.
    oidc_enabled: bool = False              # kill switch (default off)
    oidc_issuer: str | None = None         # e.g. https://auth.example.com (Authlib appends /.well-known/openid-configuration)
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    # Explicit public callback URL override. Highest priority; otherwise derived
    # from TOME_PUBLIC_URL, else the request origin (honouring X-Forwarded-Proto).
    # See backend/core/urls.py — must be the exact URL registered at the IdP.
    oidc_redirect_url: str | None = None
    oidc_scopes: str = "openid profile email groups"
    oidc_button_label: str = "Sign in with SSO"
    oidc_auto_create: bool = True          # provision unknown IdP users on first login
    oidc_groups_claim: str = "groups"      # claim carrying the user's group memberships
    oidc_admin_group: str | None = None    # group → Tome "admin"
    oidc_member_group: str | None = None   # group → Tome "member"
    oidc_guest_group: str | None = None    # group → Tome "guest"
    oidc_default_role: str = "guest"       # role when no group matches
    oidc_role_sync: str = "login"          # "login" (IdP is truth each login) | "create" (set once)
    oidc_allowed_group: str | None = None  # if set, membership required to log in at all
    oidc_post_login_redirect: str = "/auth/callback"  # frontend route that receives #token

    @property
    def oidc_configured(self) -> bool:
        """True only when SSO is enabled AND fully configured."""
        return bool(
            self.oidc_enabled
            and self.oidc_issuer
            and self.oidc_client_id
            and self.oidc_client_secret
        )

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
            try:
                return key_file.read_text().strip()
            except PermissionError as exc:
                raise RuntimeError(
                    f"Cannot read {key_file}: it was created by a different user "
                    f"(the file is mode 0600). Tome is running as uid={os.getuid()} "
                    f"gid={os.getgid()}. Either run the container with PUID/PGID "
                    f"matching the owner of your /data directory, chown /data on "
                    f"the host to the uid Tome runs as, or set TOME_SECRET_KEY "
                    f"explicitly."
                ) from exc

        new_key = secrets.token_urlsafe(64)
        try:
            key_file.write_text(new_key)
        except PermissionError as exc:
            raise RuntimeError(
                f"Cannot write {key_file}: /data is not writable by uid={os.getuid()} "
                f"gid={os.getgid()}. Run the container with PUID/PGID matching the "
                f"owner of your /data directory, or chown it on the host."
            ) from exc
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
