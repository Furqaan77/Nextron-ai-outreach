"""Pins (SC-1): exact pins in ``pyproject.toml``, the same versions in ``uv.lock``, banned packages."""

import re
import tomllib

import pytest

from tests.contracts.support import LOCKFILE, pyproject

STACK_PACKAGES = {
    "alembic",
    "anthropic",
    "apscheduler",
    "cryptography",
    "dnspython",
    "email-validator",
    "fastapi",
    "google-api-python-client",
    "google-auth-oauthlib",
    "httpx2",
    "jinja2",
    "keyring",
    "playwright",
    "protego",
    "pydantic",
    "pydantic-settings",
    "python-ulid",
    "selectolax",
    "sqlalchemy",
    "trafilatura",
    "tzdata",
    "uvicorn",
}
COMPATIBLE_RELEASE = {"pydantic-settings": "2.16"}  # the one ~= pin the spine allows
REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+)(?P<op>==|~=)(?P<version>[0-9][0-9A-Za-z.]*)$"
)


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _lock() -> dict[str, dict]:
    data = tomllib.loads(LOCKFILE.read_text(encoding="utf-8"))
    return {package["name"]: package for package in data["package"]}


def _pins(requirements: list[str]) -> dict[str, tuple[str, str]]:
    pins = {}
    for requirement in requirements:
        match = REQUIREMENT.match(requirement)
        assert match, f"{requirement!r} is not pinned with == (or the allowed ~=)"
        pins[_normalise(match["name"])] = (match["op"], match["version"])
    return pins


def test_python_range() -> None:
    assert pyproject()["project"]["requires-python"] == ">=3.14.8,<3.15"
    lock = tomllib.loads(LOCKFILE.read_text(encoding="utf-8"))
    assert lock["requires-python"] == ">=3.14.8, <3.15"


def test_every_stack_package_is_pinned_and_locked_at_that_version() -> None:
    pins = _pins(pyproject()["project"]["dependencies"])
    lock = _lock()

    assert STACK_PACKAGES <= set(pins)
    for name, (op, version) in pins.items():
        locked = lock[name]["version"]
        if name in COMPATIBLE_RELEASE:
            assert (op, version) == ("~=", COMPATIBLE_RELEASE[name])
            assert locked.startswith(version + "."), f"{name} locked at {locked}"
        else:
            assert op == "==", f"{name} must be pinned with =="
            assert locked == version, f"{name} pinned {version} but locked {locked}"


def test_dev_dependencies_are_pinned_and_locked() -> None:
    pins = _pins(pyproject()["dependency-groups"]["dev"])
    lock = _lock()

    assert pins == {
        "import-linter": ("==", "2.16"),
        "pytest": ("==", "9.2.0"),
        "pytest-xdist": ("==", "3.8.0"),
    }
    assert lock["import-linter"]["version"] == "2.16"
    assert lock["pytest"]["version"] == "9.2.0"
    assert lock["pytest-xdist"]["version"] == "3.8.0"


@pytest.mark.parametrize("banned", ["ulid-py", "aiosqlite"])
def test_banned_package_is_not_in_the_lock(banned: str) -> None:
    assert banned not in _lock()


def test_pytz_arrives_only_through_dateparser() -> None:
    """pytz is banned from nextron code (contract 4) but trafilatura 2.3.1 needs it.

    The chain is trafilatura -> htmldate -> dateparser -> pytz. This fails if pytz becomes a
    direct dependency or a second package starts pulling it in.
    """
    lock = _lock()
    dependents = {
        name
        for name, package in lock.items()
        for dependency in package.get("dependencies", [])
        if dependency["name"] == "pytz"
    }

    assert dependents <= {"dateparser"}
    assert "pytz" not in _pins(pyproject()["project"]["dependencies"])
