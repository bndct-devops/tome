import os
from pathlib import Path

import pytest

from backend.core.config import Settings


def _fresh_settings(tmp_path: Path, **env) -> Settings:
    for k in list(os.environ):
        if k.startswith("TOME_"):
            del os.environ[k]
    for k, v in env.items():
        os.environ[k] = v
    s = Settings()
    s.data_dir = tmp_path
    s.ensure_dirs()
    return s


def test_env_var_wins(tmp_path, monkeypatch):
    s = _fresh_settings(tmp_path, TOME_SECRET_KEY="operator-supplied-key")
    assert s.resolve_secret_key() == "operator-supplied-key"
    assert not s.secret_key_file.exists()


def test_auto_generate_on_first_boot(tmp_path):
    s = _fresh_settings(tmp_path)
    key = s.resolve_secret_key()
    assert len(key) >= 32
    assert s.secret_key_file.exists()
    assert s.secret_key_file.read_text().strip() == key


def test_auto_generate_is_stable_across_calls(tmp_path):
    s = _fresh_settings(tmp_path)
    first = s.resolve_secret_key()
    s2 = _fresh_settings(tmp_path)
    assert s2.resolve_secret_key() == first


def test_default_literal_is_rejected(tmp_path):
    s = _fresh_settings(tmp_path, TOME_SECRET_KEY="change-me-in-production")
    with pytest.raises(RuntimeError, match="historical default"):
        s.resolve_secret_key()
