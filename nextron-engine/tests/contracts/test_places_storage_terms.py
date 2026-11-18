"""The Google Maps Platform storage terms (Story 8.2; FR-49, AD-17, NFR-12), each with a proof.

- **Columns.** The only ``leads`` columns named ``place_*`` are ``place_id``, ``place_lat``,
  ``place_lng`` and ``place_coords_at``, and no column of any table is named like a Places
  field (a name holding ``display_name``, ``website_uri`` or ``formatted_address``, or a
  ``phone``, ``rating``, ``review`` or ``reviews`` token), unless ``ALLOWED_COLUMNS`` lists it
  with its reason (none so far).
- **Imports.** ``nextron.ports.places`` and ``nextron.adapters.places`` (which carry the
  transient ``PlaceCandidate``), and the names ``PlaceCandidate`` and ``PlaceSearchPage``
  themselves (relative imports resolved), are imported only by the
  modules ``PLACES_IMPORTERS`` allows: the ports package, the Places adapter, the search seam
  (``nextron/app/discovery/places.py``), Story 8.3's ingest
  (``nextron/app/discovery/place_ingest.py``), Story 8.4's Companies House ingest
  (``nextron/app/discovery/registry_ingest.py``, its website lookup) and ``nextron/main.py``. No module of the AI and prompt
  packages (``AI_PACKAGES``) imports them, whatever the allowlist says.
- **A planted run.** ``PlaceSearches.search_page`` runs on a database migrated to head through a
  ``GooglePlaces`` on a mock transport (metered by the real ``UsageMeter``, so ``api_usage`` rows
  are written) and through ``ScriptedPlaces``, both answering a candidate whose display name and
  website are planted strings. Afterwards no text cell of any table (``events`` and
  ``jobs.payload`` included) and no log record holds either string.
- **A planted discovery run** (Story 8.3). A ``discover_places`` job, requested through
  ``PlaceDiscovery.request_run`` and run through the registry's handler, ingests three
  candidates, each with a planted display name, a planted website path and query, and planted
  coordinates: one created, one merged (its place ID already held) and one blocked (its domain
  suppressed). Afterwards no text cell of any table (``jobs.payload``, ``events`` and
  ``discovery_runs`` included) and no log record holds a planted string or coordinate, and the
  only coordinates stored are the created Lead's numeric ``place_lat``/``place_lng``.

The AST check's proofs run it on a temporary copy of the tree with a probe module.
"""

import ast
import logging
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx2
import pytest
from sqlalchemy import Engine

import nextron.db.models  # noqa: F401 - registers every model on Base.metadata
from nextron.adapters.places import GooglePlaces
from nextron.app.discovery.places import PlaceSearches
from nextron.db.metering import UsageMeter
from nextron.db.models.base import Base
from nextron.db.uow import UnitOfWorkFactory
from nextron.ports.places import PlaceCandidate, PlaceSearchPage
from tests.contracts.support import ENGINE_ROOT, copy_with_probe
from tests.fakes.clock import FrozenClock
from tests.fakes.places import ScriptedPlaces
from tests.fakes.secrets import InMemorySecrets

PLACE_COLUMNS: Final = frozenset({"place_id", "place_lat", "place_lng", "place_coords_at"})
FORBIDDEN_FRAGMENTS: Final = ("display_name", "website_uri", "formatted_address")
FORBIDDEN_TOKENS: Final = frozenset({"phone", "rating", "review", "reviews"})
# (table, column) -> why a column named like a Places field holds no Places content
ALLOWED_COLUMNS: Final[dict[tuple[str, str], str]] = {}

PLACES_MODULES: Final = ("nextron.ports.places", "nextron.adapters.places")
PLACES_NAMES: Final = frozenset({"PlaceCandidate", "PlaceSearchPage"})
PLACES_IMPORTERS: Final = (
    "nextron/ports/",
    "nextron/adapters/places/",
    "nextron/app/discovery/places.py",
    "nextron/app/discovery/place_ingest.py",  # Story 8.3: the candidates, in memory only
    "nextron/app/discovery/registry_ingest.py",  # Story 8.4: one website lookup, in memory
    "nextron/main.py",
)
AI_PACKAGES: Final = (
    "nextron/adapters/ai/",
    "nextron/app/drafting/",
    "nextron/app/playbook/",
    "nextron/domain/ai.py",
    "nextron/domain/prompts.py",
)

T: Final = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
PLANTED_NAME: Final = "Qz7Planted Display Kx4Name"
PLANTED_SITE: Final = "https://qz7-planted-website-kx4.example.com/"
PLANTED_HOST: Final = "qz7-planted-website-kx4"
PLANTED: Final = (PLANTED_NAME, "Qz7Planted", PLANTED_SITE, PLANTED_HOST)
QUERY: Final = "accounting firm"
LOCATION: Final = "Columbus, OH"


# Columns ----------------------------------------------------------------------------------------


def column_problems(tables: dict[str, tuple[str, ...]]) -> list[str]:
    """Each ``leads`` ``place_*`` column beyond the four, and each column named like a Places
    field that ``ALLOWED_COLUMNS`` doesn't list."""
    problems = [
        f"leads.{name} is a place_* column the Maps terms don't allow"
        for name in tables.get("leads", ())
        if name.startswith("place_") and name not in PLACE_COLUMNS
    ]
    for table, columns in sorted(tables.items()):
        for name in columns:
            tokens = set(name.lower().split("_"))
            named_like = any(part in name.lower() for part in FORBIDDEN_FRAGMENTS) or bool(
                tokens & FORBIDDEN_TOKENS
            )
            if named_like and (table, name) not in ALLOWED_COLUMNS:
                problems.append(f"{table}.{name} is named like a Places field")
    return problems


def model_tables() -> dict[str, tuple[str, ...]]:
    return {
        table.name: tuple(column.name for column in table.columns)
        for table in Base.metadata.sorted_tables
    }


def test_no_table_holds_a_places_field() -> None:
    tables = model_tables()

    assert PLACE_COLUMNS <= set(tables["leads"])
    assert column_problems(tables) == []


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("leads", "place_name"),
        ("leads", "place_address"),
        ("leads", "display_name"),
        ("leads", "website_uri"),
        ("discovery_runs", "formatted_address"),
        ("candidates", "national_phone_number"),
        ("candidates", "phone"),
        ("candidates", "rating"),
        ("candidates", "user_reviews"),
    ],
)
def test_the_column_check_catches_a_places_field(table: str, column: str) -> None:
    tables = model_tables()
    tables[table] = (*tables.get(table, ()), column)

    assert len(column_problems(tables)) >= 1


# Imports ----------------------------------------------------------------------------------------


def _imported_module(node: ast.ImportFrom, package: str) -> str:
    """The absolute module ``node`` imports from, relative imports resolved in ``package``."""
    if not node.level:
        return node.module or ""
    base = package.split(".")
    base = base[: len(base) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def _modules(node: ast.AST, package: str) -> Iterator[tuple[str, frozenset[str]]]:
    """Each module a node imports, with the names it takes from it; ``package`` is the
    importing module's package, for relative imports."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name, frozenset()
    elif isinstance(node, ast.ImportFrom):
        module = _imported_module(node, package)
        names = frozenset(alias.name for alias in node.names)
        yield module, names
        for name in names:  # from nextron.ports import places
            yield f"{module}.{name}", frozenset()


def places_import_problems(root: Path) -> list[str]:
    """Each module under ``root/nextron`` that imports Places content but isn't allowed to."""
    problems = []
    for path in sorted((root / "nextron").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        package = ".".join(relative.removesuffix(".py").split("/")[:-1])
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        found = sorted(
            {
                module
                for node in ast.walk(tree)
                for module, names in _modules(node, package)
                if module in PLACES_MODULES
                or any(module.startswith(f"{places}.") for places in PLACES_MODULES)
                or names & PLACES_NAMES
            }
        )
        if not found:
            continue
        if relative.startswith(AI_PACKAGES):
            problems.append(f"{relative} is an AI or prompt module and imports {found}")
        elif not relative.startswith(PLACES_IMPORTERS):
            problems.append(f"{relative} imports {found}")
    return problems


def test_places_content_is_imported_only_where_allowed() -> None:
    assert places_import_problems(ENGINE_ROOT) == []


@pytest.mark.parametrize(
    ("package", "source"),
    [
        ("nextron.app.research", "from nextron.ports.places import PlaceCandidate\n"),
        ("nextron.app.discovery", "import nextron.ports.places\n"),
        ("nextron.db.repos", "from nextron.ports import places\n"),
        ("nextron.web", "from nextron.adapters.places import GooglePlaces\n"),
        ("nextron.app.drafting", "from nextron.ports.places import PlaceSearchPage\n"),
        ("nextron.adapters.ai", "from nextron.ports.places import PlaceCandidate\n"),
        ("nextron.domain", "from somewhere import PlaceCandidate\n"),
        ("nextron.adapters.ai", "from ...ports.places import PlaceCandidate\n"),
        ("nextron.app.research", "from ...ports import places\n"),
        ("nextron.web", "from nextron.app.discovery.places import PlaceSearchPage\n"),
    ],
)
def test_the_import_check_catches_a_probe(tmp_path: Path, package: str, source: str) -> None:
    module, root = copy_with_probe(tmp_path, package, source)

    problems = places_import_problems(root)

    probe = "/".join(module.split(".")) + ".py"
    assert [problem for problem in problems if problem.startswith(probe)] != []


def test_the_import_check_holds_an_ai_module_to_account_even_inside_the_allowlist(
    tmp_path: Path,
) -> None:
    """``nextron/ports/`` is allowed; an AI package is checked first, so a probe in the AI
    adapter is reported even though nothing else about it differs."""
    module, root = copy_with_probe(
        tmp_path, "nextron.adapters.ai", "from nextron.ports.places import PlaceCandidate\n"
    )

    [problem] = places_import_problems(root)

    assert problem.startswith("/".join(module.split(".")) + ".py is an AI or prompt module")


def test_the_port_and_the_model_agree_on_the_place_id_length() -> None:
    from nextron.db.models import leads as lead_models  # noqa: PLC0415
    from nextron.ports import places as places_port  # noqa: PLC0415

    assert places_port.PLACE_ID_LENGTH == lead_models.PLACE_ID_LENGTH


# A planted run ----------------------------------------------------------------------------------


def planted_candidate() -> PlaceCandidate:
    return PlaceCandidate("ChIJ-planted", PLANTED_SITE, PLANTED_NAME, 39.9612, -82.9988)


def text_cells(path: Path) -> Iterator[tuple[str, str, str]]:
    """Every text or blob cell of every table, as ``(table, column, text)``."""
    reader = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in reader.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        for table in tables:
            cursor = reader.execute(f'SELECT * FROM "{table}"')  # noqa: S608 - names from SQLite
            columns = [description[0] for description in cursor.description]
            for row in cursor:
                for column, value in zip(columns, row, strict=True):
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", errors="replace")
                    if isinstance(value, str):
                        yield table, column, value
    finally:
        reader.close()


def test_a_search_stores_and_logs_no_places_content(
    db_engine: Engine, units: UnitOfWorkFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    clock = FrozenClock(T)
    meter = UsageMeter(units, clock)
    body = {
        "places": [
            {
                "id": "ChIJ-planted",
                "websiteUri": PLANTED_SITE,
                "displayName": {"text": PLANTED_NAME},
                "location": {"latitude": 39.9612, "longitude": -82.9988},
            }
        ]
    }
    secrets = InMemorySecrets()
    secrets.write("google_places/api_key", "Gp4Yt8Rm2Kx6Wq1Zv9Ld")
    google = GooglePlaces(
        secrets,
        meter=meter,
        http=httpx2.Client(
            transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=body))
        ),
    )
    scripted = ScriptedPlaces(
        {(QUERY, LOCATION, None): PlaceSearchPage((planted_candidate(),), None)}
    )

    pages = []
    for places in (google, scripted):
        with units.open():
            pages.append(PlaceSearches(clock, meter, places).search_page(QUERY, LOCATION))

    assert [page.candidates[0].display_name for page in pages if page is not None] == [
        PLANTED_NAME,
        PLANTED_NAME,
    ]
    database = db_engine.url.database
    assert database is not None
    cells = list(text_cells(Path(database)))
    assert any(table == "api_usage" for table, _, _ in cells)  # the request was metered
    leaked = [
        (table, column)
        for table, column, text in cells
        if any(planted in text for planted in PLANTED)
    ]
    assert leaked == []
    for record in caplog.records:
        shown = record.getMessage() + repr(vars(record))
        assert not any(planted in shown for planted in PLANTED), record.getMessage()


PLANTED_PATH: Final = "qz7plantedpath"
PLANTED_QUERY: Final = "kx4plantedquery"
PLANTED_LAT: Final = 39.961234567
PLANTED_LNG: Final = -82.998765432
PLANTED_RUN: Final = (
    PLANTED_NAME,
    "Qz7Planted",
    PLANTED_PATH,
    PLANTED_QUERY,
    "39.961234",
    "82.998765",
)


def test_a_discovery_run_stores_and_logs_no_places_content(
    db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    from nextron.app.compliance.api import Compliance  # noqa: PLC0415
    from nextron.domain.contactability import DomainSubject, SuppressionScope  # noqa: PLC0415
    from nextron.domain.job import Job, Lane, decode_payload  # noqa: PLC0415
    from nextron.domain.suppression_entry import SuppressionSource  # noqa: PLC0415
    from tests.app.test_place_discovery import LAW, build_world  # noqa: PLC0415

    caplog.set_level(logging.DEBUG)
    world = build_world(db_engine)
    world.lead(place_id="ChIJ-held", web_domain="held-firm.com")
    world.transact(
        lambda: Compliance(world.clock).suppress(
            DomainSubject("blocked-firm.com"),
            SuppressionScope.FIRM,
            SuppressionSource.OPERATOR,
            None,
        )
    )
    for domain in ("acme-law.com", "blocked-firm.com"):
        world.site(domain, f"<title>{domain.split('.')[0]}</title>")

    def planted(place_id: str, domain: str) -> PlaceCandidate:
        website = f"https://www.{domain}/{PLANTED_PATH}?ref={PLANTED_QUERY}"
        return PlaceCandidate(place_id, website, PLANTED_NAME, PLANTED_LAT, PLANTED_LNG)

    world.search(
        PlaceSearchPage(
            (
                planted("ChIJ-new", "acme-law.com"),
                planted("ChIJ-held", "held-firm.com"),
                planted("ChIJ-blocked", "blocked-firm.com"),
            ),
            None,
        ),
        query=LAW,
    )
    world.request()
    [(_, payload, _, _)] = world.jobs("discover_places")
    job = Job("01JD0000000000000000000099", "discover_places", Lane.BULK, decode_payload(payload), 1)

    with world.units.open():
        assert world.handler()(job) is None

    assert world.counts() == {
        "seen": 3,
        "created": 1,
        "merged": 1,
        "suppressed": 1,
        "no_website": 0,
        "market_blocked": 0,
    }
    database = db_engine.url.database
    assert database is not None
    cells = list(text_cells(Path(database)))
    assert {table for table, _, _ in cells} >= {"jobs", "events", "discovery_runs", "leads"}
    leaked = [
        (table, column)
        for table, column, text in cells
        if any(planted in text for planted in PLANTED_RUN)
    ]
    assert leaked == []
    assert world.sql(
        "SELECT place_id, place_lat, place_lng FROM leads WHERE place_lat IS NOT NULL"
    ) == [("ChIJ-new", PLANTED_LAT, PLANTED_LNG)]
    for record in caplog.records:
        shown = record.getMessage() + repr(vars(record))
        assert not any(planted in shown for planted in PLANTED_RUN), record.getMessage()
