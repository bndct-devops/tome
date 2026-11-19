"""Tome's own info lines reach the log; libraries stay at WARNING."""
import logging

from backend.core.config import Settings
from backend.core.logging_setup import configure_logging


def test_backend_info_is_enabled_and_libraries_are_not(monkeypatch):
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", logging.WARNING)
    configure_logging("INFO")
    assert len(root.handlers) == 1
    assert logging.getLogger("backend.services.auto_import").isEnabledFor(logging.INFO)
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)


def test_info_line_is_written_with_time_and_level(monkeypatch, capsys):
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    configure_logging("INFO")
    logging.getLogger("backend.example").info("auto-import picked up %d files", 3)
    err = capsys.readouterr().err
    assert "INFO backend.example: auto-import picked up 3 files" in err


def test_an_existing_root_handler_is_left_alone(monkeypatch):
    root = logging.getLogger()
    mine = logging.NullHandler()
    monkeypatch.setattr(root, "handlers", [mine])
    configure_logging("DEBUG")
    assert root.handlers == [mine]
    assert logging.getLogger("backend").level == logging.DEBUG


def test_level_setting_is_normalised(monkeypatch):
    monkeypatch.setenv("TOME_LOG_LEVEL", "debug")
    assert Settings().log_level == "DEBUG"
    monkeypatch.setenv("TOME_LOG_LEVEL", "chatty")
    assert Settings().log_level == "INFO"
