"""The lock files cover every dependency pyproject.toml declares.

The image installs requirements.lock and CI installs requirements-dev.lock,
then Tome itself with --no-deps: a dependency added to pyproject.toml without
refreshing the locks would be missing from both. Refresh with the commands in
docs/releasing.md ("Dependencies").
"""
import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _name(requirement: str) -> str:
    return re.sub(r"[-_.]+", "-", re.split(r"[\[<>=!~; ]", requirement, maxsplit=1)[0]).lower()


def _locked(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;]+)", line)
        if m:
            pins[_name(m[1])] = m[2]
    return pins


def _declared() -> tuple[set[str], set[str]]:
    project = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
    runtime = {_name(r) for r in project["dependencies"]}
    dev = {_name(r) for r in project["optional-dependencies"]["dev"]}
    return runtime, dev


def test_runtime_lock_covers_every_dependency():
    runtime, _ = _declared()
    assert runtime <= set(_locked(REPO / "requirements.lock"))


def test_dev_lock_covers_runtime_and_dev_dependencies():
    runtime, dev = _declared()
    assert runtime | dev <= set(_locked(REPO / "requirements-dev.lock"))


def test_both_locks_pin_the_same_runtime_versions():
    runtime_lock = _locked(REPO / "requirements.lock")
    dev_lock = _locked(REPO / "requirements-dev.lock")
    assert {k: dev_lock.get(k) for k in runtime_lock} == runtime_lock
