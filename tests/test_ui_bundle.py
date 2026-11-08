"""TOME_UI picks which built frontend the server serves."""
from pathlib import Path

from backend.main import frontend_dist_dir


def test_new_is_the_default(tmp_path: Path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist-classic").mkdir()
    assert frontend_dist_dir(tmp_path, "new") == tmp_path / "dist"
    assert frontend_dist_dir(tmp_path, "") == tmp_path / "dist"


def test_classic_when_built(tmp_path: Path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist-classic").mkdir()
    assert frontend_dist_dir(tmp_path, "classic") == tmp_path / "dist-classic"
    assert frontend_dist_dir(tmp_path, "Classic") == tmp_path / "dist-classic"


def test_classic_falls_back_when_missing(tmp_path: Path, caplog):
    (tmp_path / "dist").mkdir()
    assert frontend_dist_dir(tmp_path, "classic") == tmp_path / "dist"
    assert "TOME_UI=classic" in caplog.text
