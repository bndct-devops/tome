"""TOME_<NAME>_FILE: secrets read from a file (Docker/Kubernetes secrets)."""
import os

import pytest

from backend.core.config import FILE_SECRETS, Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("TOME_"):
            monkeypatch.delenv(k)


def _settings(tmp_path, **kwargs) -> Settings:
    s = Settings(_env_file=None, **kwargs)
    s.data_dir = tmp_path
    s.ensure_dirs()
    return s


@pytest.mark.parametrize("name", FILE_SECRETS)
def test_value_from_file(tmp_path, monkeypatch, name):
    f = tmp_path / "secret"
    f.write_text("from-file")
    monkeypatch.setenv(f"TOME_{name.upper()}_FILE", str(f))
    assert getattr(Settings(_env_file=None), name) == "from-file"


@pytest.mark.parametrize("content", ["s3cret\n", "s3cret\r\n"])
def test_trailing_newline_stripped(tmp_path, monkeypatch, content):
    f = tmp_path / "secret"
    f.write_bytes(content.encode())
    monkeypatch.setenv("TOME_SMTP_PASSWORD_FILE", str(f))
    assert Settings(_env_file=None).smtp_password == "s3cret"


def test_inner_whitespace_kept(tmp_path, monkeypatch):
    f = tmp_path / "secret"
    f.write_text(" pass word \n")
    monkeypatch.setenv("TOME_SMTP_PASSWORD_FILE", str(f))
    assert Settings(_env_file=None).smtp_password == " pass word "


def test_both_set_is_an_error(tmp_path, monkeypatch):
    f = tmp_path / "secret"
    f.write_text("from-file")
    monkeypatch.setenv("TOME_HARDCOVER_TOKEN", "from-env")
    monkeypatch.setenv("TOME_HARDCOVER_TOKEN_FILE", str(f))
    with pytest.raises(RuntimeError, match="TOME_HARDCOVER_TOKEN and TOME_HARDCOVER_TOKEN_FILE"):
        Settings(_env_file=None)


def test_missing_file_is_an_error(tmp_path, monkeypatch):
    missing = tmp_path / "nope"
    monkeypatch.setenv("TOME_OIDC_CLIENT_SECRET_FILE", str(missing))
    with pytest.raises(RuntimeError, match="TOME_OIDC_CLIENT_SECRET_FILE") as exc:
        Settings(_env_file=None)
    assert str(missing) in str(exc.value)
    assert isinstance(exc.value.__cause__, OSError)


def test_empty_file_var_is_unset(monkeypatch):
    monkeypatch.setenv("TOME_GOOGLE_BOOKS_KEY_FILE", "")
    monkeypatch.setenv("TOME_GOOGLE_BOOKS_KEY", "from-env")
    assert Settings(_env_file=None).google_books_key == "from-env"


def test_file_var_from_dotenv(tmp_path):
    f = tmp_path / "secret"
    f.write_text("from-file\n")
    env = tmp_path / ".env"
    env.write_text(f"TOME_SMTP_USER_FILE={f}\n")
    assert Settings(_env_file=env).smtp_user == "from-file"


def test_file_var_from_dotenv_is_case_insensitive(tmp_path):
    f = tmp_path / "secret"
    f.write_text("from-file\n")
    env = tmp_path / ".env"
    env.write_text(f"tome_smtp_user_file={f}\n")
    assert Settings(_env_file=env).smtp_user == "from-file"


def test_empty_file_var_is_none_not_cwd(monkeypatch):
    # Path("") would be Path("."), the current directory.
    monkeypatch.setenv("TOME_SMTP_USER_FILE", "")
    s = Settings(_env_file=None)
    assert s.smtp_user_file is None
    assert s.smtp_user is None


def test_secret_key_env_beats_data_dir_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TOME_SECRET_KEY", "from-env")
    s = _settings(tmp_path)
    s.generated_key_path.write_text("from-data-dir")
    assert s.resolve_secret_key() == "from-env"


def test_secret_key_file_beats_data_dir_file(tmp_path, monkeypatch):
    f = tmp_path / "tome_secret_key"
    f.write_text("from-file\n")
    monkeypatch.setenv("TOME_SECRET_KEY_FILE", str(f))
    s = _settings(tmp_path)
    s.generated_key_path.write_text("from-data-dir")
    assert s.resolve_secret_key() == "from-file"


def test_secret_key_data_dir_file_without_env(tmp_path):
    s = _settings(tmp_path)
    s.generated_key_path.write_text("from-data-dir")
    assert s.resolve_secret_key() == "from-data-dir"


def test_secret_key_env_and_file_is_an_error(tmp_path, monkeypatch):
    f = tmp_path / "tome_secret_key"
    f.write_text("from-file")
    monkeypatch.setenv("TOME_SECRET_KEY", "from-env")
    monkeypatch.setenv("TOME_SECRET_KEY_FILE", str(f))
    with pytest.raises(RuntimeError, match="TOME_SECRET_KEY and TOME_SECRET_KEY_FILE"):
        Settings(_env_file=None)


def test_default_literal_via_file_is_rejected(tmp_path, monkeypatch):
    f = tmp_path / "tome_secret_key"
    f.write_text("change-me-in-production\n")
    monkeypatch.setenv("TOME_SECRET_KEY_FILE", str(f))
    s = _settings(tmp_path)
    with pytest.raises(RuntimeError, match="historical default"):
        s.resolve_secret_key()
