"""The tree matches the spine's source tree and owner map (Structural Seed, AD-3)."""

import pytest

from nextron.db.owners import OWNERS
from tests.contracts.support import ENGINE_ROOT, PACKAGE

SPINE_OWNERS = {
    "setup",
    "discovery",
    "research",
    "scoring",
    "sequencing",
    "drafting",
    "approval",
    "sending",
    "replies",
    "pipeline",
    "compliance",
    "playbook",
    "notify",
    "jobqueue",
    "ops",
    "analytics",
}

FILES = [
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "README.md",
    "nextron/__init__.py",
    "nextron/__main__.py",
    "nextron/main.py",
    "nextron/domain/__init__.py",
    "nextron/domain/ids.py",
    "nextron/domain/notifications.py",
    "nextron/ports/__init__.py",
    "nextron/ports/clock.py",
    "nextron/ports/errors.py",
    "nextron/ports/secrets.py",
    "nextron/domain/open_code.py",
    "nextron/db/__init__.py",
    "nextron/db/engine.py",
    "nextron/db/uow.py",
    "nextron/db/eventlog.py",
    "nextron/db/migrations.py",
    "nextron/db/owners.py",
    "nextron/db/pii_registry.py",
    "nextron/db/models/__init__.py",
    "nextron/db/models/base.py",
    "nextron/db/models/events.py",
    "nextron/db/alembic/env.py",
    "nextron/db/alembic/versions/0001_1_3_persistence_foundation.py",
    "nextron/db/repos/__init__.py",
    "nextron/app/__init__.py",
    "nextron/app/queries/__init__.py",
    "nextron/adapters/__init__.py",
    "nextron/adapters/ai/__init__.py",
    "nextron/adapters/clock/__init__.py",
    "nextron/adapters/secrets/__init__.py",
    "nextron/jobs/__init__.py",
    "nextron/web/__init__.py",
    "nextron/web/app.py",
    "nextron/web/csrf.py",
    "nextron/web/guard.py",
    "nextron/web/routing.py",
    "nextron/web/sessions.py",
    "nextron/web/templating.py",
    "nextron/web/templates/base.html",
    "nextron/web/templates/home.html",
    "nextron/web/templates/locked.html",
    "nextron/web/templates/macros.html",
    "nextron/web/static/htmx-2.0.11.min.js",
    "nextron/web/static/app.css",
    "workers/unsubscribe/.gitkeep",
    "scripts/gen_owner_contracts.py",
    "tests/fakes/clock.py",
    "tests/fakes/secrets.py",
    "tests/fakes/units.py",
    "tests/contracts/safe_allowlist.txt",
]
FOLDERS = [
    "nextron/db/alembic/versions",
    "nextron/web/templates",
    "seed",
    "workers/unsubscribe",
    "scripts",
    "tests/unit",
    "tests/app",
    "tests/contracts",
    "tests/fakes",
    "tests/ai_sets",
]


@pytest.mark.parametrize("relative", FILES)
def test_file_exists(relative: str) -> None:
    assert (ENGINE_ROOT / relative).is_file()


@pytest.mark.parametrize("relative", FOLDERS)
def test_folder_exists(relative: str) -> None:
    assert (ENGINE_ROOT / relative).is_dir()


def test_owners_are_the_spine_owner_map() -> None:
    assert len(OWNERS) == len(set(OWNERS))
    assert set(OWNERS) == SPINE_OWNERS


@pytest.mark.parametrize("owner", sorted(SPINE_OWNERS))
def test_each_owner_has_api_queries_and_repositories(owner: str) -> None:
    assert (PACKAGE / "app" / owner / "__init__.py").is_file()
    assert (PACKAGE / "app" / owner / "api.py").is_file()
    assert (PACKAGE / "app" / "queries" / f"{owner}.py").is_file()
    assert (PACKAGE / "db" / "repos" / owner / "__init__.py").is_file()


def test_app_holds_only_owner_packages_and_queries() -> None:
    packages = {p.name for p in (PACKAGE / "app").iterdir() if (p / "__init__.py").is_file()}
    queries = {p.stem for p in (PACKAGE / "app" / "queries").glob("*.py")} - {"__init__"}

    assert packages == SPINE_OWNERS | {"queries"}
    assert queries == SPINE_OWNERS


def test_every_package_docstring_states_its_import_rule() -> None:
    layers = [
        "domain",
        "ports",
        "db",
        "app",
        "adapters",
        "adapters/ai",
        "adapters/clock",
        "adapters/secrets",
        "jobs",
        "web",
    ]
    for layer in layers:
        text = (PACKAGE / layer / "__init__.py").read_text(encoding="utf-8")
        assert "Import rule" in text, f"nextron/{layer}/__init__.py lacks its import rule"
