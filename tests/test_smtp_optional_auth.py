"""SMTP without authentication (#271): user and password are optional."""
import os
from unittest.mock import MagicMock, patch

import pytest

from backend.core.config import Settings
from backend.services import email


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("TOME_"):
            monkeypatch.delenv(k)


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_host_only_is_not_configured():
    assert _settings(smtp_host="smtp.example.com").smtp_configured is False


def test_host_and_from_is_configured():
    s = _settings(smtp_host="smtp.example.com", smtp_from="tome@example.com")
    assert s.smtp_configured is True
    assert s.smtp_from_address == "tome@example.com"


def test_host_user_password_is_configured():
    s = _settings(smtp_host="smtp.example.com", smtp_user="u@example.com", smtp_password="pw")
    assert s.smtp_configured is True
    assert s.smtp_from_address == "u@example.com"


def test_half_set_credentials_are_not_configured():
    s = _settings(smtp_host="smtp.example.com", smtp_from="tome@example.com", smtp_user="u@example.com")
    assert s.smtp_configured is False


def _patched_settings(monkeypatch, **kwargs):
    for key, value in kwargs.items():
        monkeypatch.setattr(email.settings, key, value)


def test_connect_skips_login_without_user(monkeypatch):
    _patched_settings(
        monkeypatch,
        smtp_host="smtp.example.com", smtp_port=25, smtp_user=None, smtp_password=None,
        smtp_from="tome@example.com", smtp_use_tls=False, smtp_use_ssl=False,
    )
    conn = MagicMock()
    with patch("smtplib.SMTP", return_value=conn) as smtp_cls:
        assert email._connect() is conn
    smtp_cls.assert_called_once_with("smtp.example.com", 25, timeout=30)
    conn.login.assert_not_called()


def test_connect_logs_in_with_user(monkeypatch):
    _patched_settings(
        monkeypatch,
        smtp_host="smtp.example.com", smtp_port=25, smtp_user="u@example.com", smtp_password="pw",
        smtp_from=None, smtp_use_tls=False, smtp_use_ssl=False,
    )
    conn = MagicMock()
    with patch("smtplib.SMTP", return_value=conn):
        email._connect()
    conn.login.assert_called_once_with("u@example.com", "pw")
