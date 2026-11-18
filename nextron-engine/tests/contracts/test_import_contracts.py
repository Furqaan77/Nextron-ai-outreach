"""import-linter contracts (AD-1): all seven groups kept, and proven live."""

import re
from pathlib import Path

import pytest

from tests.contracts.support import (
    contract,
    contract_statuses,
    contracts,
    lint_copy_with_probe,
    pyproject,
    run_lint_imports,
)

SDKS_ONLY_IN_ADAPTERS = "c4-sdks-only-in-adapters"
BANNED_EVERYWHERE = "c4-banned-everywhere"
ANTHROPIC_ONLY_IN_AI = "c5-anthropic-only-in-ai"
HELPERS = "c1-composition-root-helpers"


def _group(contract_id: str) -> int:
    match = re.match(r"c(\d)-", contract_id)
    assert match, f"contract id {contract_id!r} must start with c<group>-"
    return int(match[1])


def test_graph_settings() -> None:
    settings = pyproject()["tool"]["importlinter"]
    assert settings["root_packages"] == ["nextron"]
    assert settings["include_external_packages"] is True
    assert settings["exclude_type_checking_imports"] is True


def test_lint_imports_keeps_every_contract_in_all_seven_groups() -> None:
    result = run_lint_imports()

    assert result.returncode == 0, result.stdout + result.stderr
    statuses = contract_statuses(result.stdout)
    assert statuses == {c["name"]: "KEPT" for c in contracts()}
    assert {_group(c["id"]) for c in contracts()} == set(range(1, 8))


def test_contracts_one_to_six_are_written_as_in_ad1() -> None:
    assert contract("c1-layers")["layers"] == [
        "nextron.main",
        "nextron.web | nextron.jobs | nextron.adapters",
        "nextron.app",
        "nextron.ports | nextron.db",
        "nextron.domain",
    ]
    c2 = contract("c2-delivery")
    assert (set(c2["source_modules"]), set(c2["forbidden_modules"])) == (
        {"nextron.web", "nextron.jobs"},
        {"nextron.adapters", "nextron.db"},
    )
    assert c2["allow_indirect_imports"] is True
    c3 = contract("c3-adapters")
    assert (c3["source_modules"], set(c3["forbidden_modules"])) == (
        ["nextron.adapters"],
        {"nextron.app", "nextron.db"},
    )
    assert set(contract(SDKS_ONLY_IN_ADAPTERS)["forbidden_modules"]) == {
        "anthropic",
        "googleapiclient",
        "google_auth_oauthlib",
        "google",
        "httpx2",
        "playwright",
        "keyring",
        "cryptography",
        "selectolax",
        "trafilatura",
        "protego",
        "dns",
        "email_validator",
    }
    banned = contract(BANNED_EVERYWHERE)
    assert banned["source_modules"] == ["nextron"]
    assert set(banned["forbidden_modules"]) == {
        "pytz",
        "dateutil",
        "linkedin_api",
        "instaloader",
        "instagrapi",
        "selenium",
        "pyautogui",
        "undetected_chromedriver",
        "linkedin",
        "seleniumwire",
        "playwright_stealth",
        "linkedin_scraper",
        "selenium_stealth",
        "undetected_playwright",
        "pyppeteer_stealth",
        "instagram_private_api",
    }
    assert contract(ANTHROPIC_ONLY_IN_AI)["forbidden_modules"] == ["anthropic"]
    c6 = contract("c6-app-dag")
    assert c6["containers"] == ["nextron.app"]
    assert c6["layers"] == [
        "replies | discovery | scoring | ops | analytics",
        "pipeline",
        "sequencing",
        "approval",
        "drafting | sending | research",
        "compliance | playbook | setup",
        "notify",
        "jobqueue",
        "queries",
    ]


def test_anthropic_in_the_domain_breaks_lint(tmp_path: Path) -> None:
    module, result = lint_copy_with_probe(tmp_path, "nextron.domain", "import anthropic\n")

    assert result.returncode != 0
    assert f"{module} -> anthropic" in result.stdout
    statuses = contract_statuses(result.stdout)
    assert statuses[contract(SDKS_ONLY_IN_ADAPTERS)["name"]] == "BROKEN"
    assert statuses[contract(ANTHROPIC_ONLY_IN_AI)["name"]] == "BROKEN"


# The probe goes into nextron.adapters.ai, the package allowed the most, to show the ban
# reaches every nextron module.
@pytest.mark.parametrize(
    ("statement", "banned"),
    [
        ("import pytz", "pytz"),
        ("import dateutil.tz", "dateutil"),
        ("from dateutil import tz", "dateutil"),
        ("from dateutil.tz import gettz", "dateutil"),
        ("import linkedin_api", "linkedin_api"),
        ("import instaloader", "instaloader"),
        ("from instagrapi import Client", "instagrapi"),
        ("from selenium import webdriver", "selenium"),
        ("import pyautogui", "pyautogui"),
        ("import undetected_chromedriver as uc", "undetected_chromedriver"),
        ("import linkedin", "linkedin"),
        ("from linkedin import linkedin", "linkedin"),
        ("from seleniumwire import webdriver", "seleniumwire"),
        ("from playwright_stealth import stealth_sync", "playwright_stealth"),
        ("from linkedin_scraper import Person", "linkedin_scraper"),
        ("from selenium_stealth import stealth", "selenium_stealth"),
        ("import undetected_playwright", "undetected_playwright"),
        ("from pyppeteer_stealth import stealth", "pyppeteer_stealth"),
        ("from instagram_private_api import Client", "instagram_private_api"),
    ],
)
def test_banned_libraries_break_lint_in_any_module(
    statement: str, banned: str, tmp_path: Path
) -> None:
    module, result = lint_copy_with_probe(tmp_path, "nextron.adapters.ai", statement + "\n")

    assert result.returncode != 0
    assert f"{module} -> {banned} (l.1)" in result.stdout
    statuses = contract_statuses(result.stdout)
    assert statuses[contract(BANNED_EVERYWHERE)["name"]] == "BROKEN"


@pytest.mark.parametrize(
    ("package", "statement", "imported", "contract_id"),
    [
        ("nextron.web", "import nextron.bootstrap", "nextron.bootstrap", HELPERS),
        ("nextron.app", "from nextron import lock", "nextron.lock", HELPERS),
        ("nextron.domain", "import nextron.startup_checks", "nextron.startup_checks", HELPERS),
        ("nextron.ports", "import nextron.logconfig", "nextron.logconfig", HELPERS),
        ("nextron.app", "import sqlalchemy", "sqlalchemy", "c4-sqlalchemy-only-in-db"),
        ("nextron.web", "import alembic", "alembic", "c4-sqlalchemy-only-in-db"),
        ("nextron.app", "import apscheduler", "apscheduler", "c4-apscheduler-only-in-jobs"),
        (
            "nextron.web",
            "import pydantic_settings",
            "pydantic_settings",
            "c4-pydantic-settings-only-in-composition-root",
        ),
        ("nextron.jobs", "import fastapi", "fastapi", "c4-web-framework-only-in-web-and-main"),
        ("nextron.app", "import uvicorn", "uvicorn", "c4-web-framework-only-in-web-and-main"),
        (
            "nextron.adapters",
            "import starlette",
            "starlette",
            "c4-web-framework-only-in-web-and-main",
        ),
    ],
)
def test_placement_contracts_break_lint(
    package: str, statement: str, imported: str, contract_id: str, tmp_path: Path
) -> None:
    module, result = lint_copy_with_probe(tmp_path, package, statement + "\n")

    assert result.returncode != 0
    assert f"{module} -> {imported} (l.1)" in result.stdout
    assert contract_statuses(result.stdout)[contract(contract_id)["name"]] == "BROKEN"


APP_DB_SURFACE = "c1-app-db-surface"
DB_INTERNALS = [
    "nextron.db.engine",
    "nextron.db.migrations",
    "nextron.db.models",
    "nextron.db.owners",
    "nextron.db.pii_registry",
]


def test_the_app_db_surface_contract_is_written_as_in_the_story() -> None:
    surface = contract(APP_DB_SURFACE)

    assert surface["type"] == "forbidden"
    assert surface["source_modules"] == ["nextron.app"]
    assert surface["forbidden_modules"] == DB_INTERNALS
    assert surface["allow_indirect_imports"] is True
    ids = [c["id"] for c in contracts()]
    assert ids.index(APP_DB_SURFACE) == ids.index(HELPERS) + 1


def test_the_app_importing_db_internals_breaks_lint(tmp_path: Path) -> None:
    source = "".join(f"import {module}\n" for module in DB_INTERNALS)
    module, result = lint_copy_with_probe(tmp_path, "nextron.app.setup", source)

    assert result.returncode != 0
    for line, imported in enumerate(DB_INTERNALS, start=1):
        assert f"{module} -> {imported} (l.{line})" in result.stdout
    statuses = contract_statuses(result.stdout)
    assert statuses[contract(APP_DB_SURFACE)["name"]] == "BROKEN"


def test_the_app_may_use_uow_eventlog_and_its_own_repositories(tmp_path: Path) -> None:
    source = (
        "from nextron.db import eventlog, uow\n"
        "from nextron.db.uow import command, current, use_case\n"
        "import nextron.db.repos.setup\n"
    )
    _, result = lint_copy_with_probe(tmp_path, "nextron.app.setup", source)

    assert result.returncode == 0, result.stdout


def test_type_checking_imports_are_excluded(tmp_path: Path) -> None:
    source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import anthropic\n"
    _, result = lint_copy_with_probe(tmp_path, "nextron.domain", source)

    assert result.returncode == 0, result.stdout
