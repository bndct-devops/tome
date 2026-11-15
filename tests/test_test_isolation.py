"""The test run never touches the developer's real folders or .env."""
import tomllib
from pathlib import Path

from backend import __version__
from backend.core.config import settings

REPO = Path(__file__).resolve().parent.parent


def test_settings_folders_are_throwaway():
    for folder in (settings.data_dir, settings.library_dir, settings.incoming_dir):
        resolved = Path(folder).resolve()
        assert REPO not in resolved.parents and resolved != REPO, folder
        assert "tome-tests-" in str(resolved)


def test_database_is_not_the_development_one():
    assert (REPO / "data").resolve() not in Path(settings.db_path).resolve().parents


def test_dotenv_is_ignored():
    assert not Path(settings.model_config["env_file"]).exists()


# --- one version number -----------------------------------------------------

def test_api_docs_carry_the_package_version(client):
    # The installed package version (pyproject.toml at install time), the
    # same number /api/health and the backup header report.
    assert client.app.version == __version__


def test_the_package_version_has_a_changelog_heading():
    """The What's New panel reads the notes for the running version from the
    changelog, so a version without a heading shows nothing."""
    declared = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    assert f"## [{declared}]" in (REPO / "CHANGELOG.md").read_text()
