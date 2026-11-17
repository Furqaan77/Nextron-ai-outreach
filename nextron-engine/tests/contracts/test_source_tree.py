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
    "nextron/ports/alert.py",
    "nextron/ports/mail.py",
    "nextron/ports/google_oauth.py",
    "nextron/ports/usage_meter.py",
    "nextron/domain/open_code.py",
    "nextron/domain/settings_schema.py",
    "nextron/domain/credentials.py",
    "nextron/domain/sink.py",
    "nextron/domain/google_oauth.py",
    "nextron/domain/machines.py",
    "nextron/domain/job.py",
    "nextron/domain/alert.py",
    "nextron/domain/budget.py",
    "nextron/domain/readiness.py",
    "nextron/domain/health.py",
    "nextron/domain/backup.py",
    "nextron/db/__init__.py",
    "nextron/db/engine.py",
    "nextron/db/uow.py",
    "nextron/db/eventlog.py",
    "nextron/db/metering.py",
    "nextron/db/migrations.py",
    "nextron/db/owners.py",
    "nextron/db/pii_registry.py",
    "nextron/db/models/__init__.py",
    "nextron/db/models/base.py",
    "nextron/db/models/events.py",
    "nextron/db/models/setup.py",
    "nextron/db/models/jobqueue.py",
    "nextron/db/models/ops.py",
    "nextron/db/models/notify.py",
    "nextron/db/models/metering.py",
    "nextron/db/alembic/env.py",
    "nextron/db/alembic/versions/0001_1_3_persistence_foundation.py",
    "nextron/db/alembic/versions/0002_1_6_settings.py",
    "nextron/db/alembic/versions/0003_1_10_jobs.py",
    "nextron/db/alembic/versions/0004_1_11_heartbeat.py",
    "nextron/db/alembic/versions/0005_1_12_notify.py",
    "nextron/db/alembic/versions/0006_1_13_metering.py",
    "nextron/db/alembic/versions/0007_1_16_backups.py",
    "nextron/db/repos/__init__.py",
    "nextron/db/repos/setup/settings.py",
    "nextron/db/repos/jobqueue/jobs.py",
    "nextron/db/repos/ops/heartbeat.py",
    "nextron/db/repos/ops/backups.py",
    "nextron/db/repos/ops/snapshot.py",
    "nextron/db/repos/notify/alerts.py",
    "nextron/db/repos/notify/notifications.py",
    "nextron/app/__init__.py",
    "nextron/app/queries/__init__.py",
    "nextron/adapters/__init__.py",
    "nextron/adapters/ai/__init__.py",
    "nextron/adapters/clock/__init__.py",
    "nextron/adapters/google_oauth/__init__.py",
    "nextron/adapters/secrets/__init__.py",
    "nextron/adapters/sink/__init__.py",
    "nextron/adapters/telegram/__init__.py",
    "nextron/jobs/__init__.py",
    "nextron/jobs/runner.py",
    "nextron/jobs/triggers.py",
    "nextron/jobs/scheduler.py",
    "nextron/jobs/heartbeat.py",
    "nextron/jobs/handlers.py",
    "nextron/web/__init__.py",
    "nextron/web/app.py",
    "nextron/web/csrf.py",
    "nextron/web/forms.py",
    "nextron/web/guard.py",
    "nextron/web/routing.py",
    "nextron/web/sessions.py",
    "nextron/web/settings_page.py",
    "nextron/web/credentials_page.py",
    "nextron/web/google_page.py",
    "nextron/web/quotas_page.py",
    "nextron/web/health_page.py",
    "nextron/web/templating.py",
    "nextron/web/templates/base.html",
    "nextron/web/templates/credentials.html",
    "nextron/web/templates/credential_row.html",
    "nextron/web/templates/google_client_row.html",
    "nextron/web/templates/google_grant_row.html",
    "nextron/web/templates/health.html",
    "nextron/web/templates/health_panel.html",
    "nextron/web/templates/home.html",
    "nextron/web/templates/locked.html",
    "nextron/web/templates/macros.html",
    "nextron/web/templates/notifications.html",
    "nextron/web/templates/quotas.html",
    "nextron/web/templates/settings.html",
    "nextron/web/templates/settings_form.html",
    "nextron/web/static/htmx-2.0.11.min.js",
    "nextron/web/static/app.css",
    "workers/unsubscribe/.gitkeep",
    "scripts/gen_owner_contracts.py",
    "tests/fakes/__init__.py",
    "tests/fakes/alert.py",
    "tests/fakes/clock.py",
    "tests/fakes/google_oauth.py",
    "tests/fakes/mail.py",
    "tests/fakes/secrets.py",
    "tests/fakes/units.py",
    "tests/fakes/usage_meter.py",
    "tests/live_guard.py",
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
        "adapters/google_oauth",
        "adapters/secrets",
        "adapters/sink",
        "adapters/telegram",
        "jobs",
        "web",
    ]
    for layer in layers:
        text = (PACKAGE / layer / "__init__.py").read_text(encoding="utf-8")
        assert "Import rule" in text, f"nextron/{layer}/__init__.py lacks its import rule"
