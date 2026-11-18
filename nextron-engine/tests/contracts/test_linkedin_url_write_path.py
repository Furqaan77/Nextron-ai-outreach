"""Where a LinkedIn URL comes from, and that it is never fetched (FR-49, Story 10.1).

- **Sources.** ``LINKEDIN_URL_SOURCES`` is pinned to the Signal Inbox, the Lead's website, a
  CSV import, a referral and an Operator entry. ``Research.add_contact`` refuses a candidate
  with a ``linkedin_url`` from any other source (``ValueError``, nothing written) and stores one
  from these with its Provenance Record.
- **Write paths.** Every ``ContactCandidate`` that may carry a ``linkedin_url`` (by keyword, by
  position, through ``replace``, or through ``*``/``**`` unpacking, which may hold one) is in an
  allowlist naming the source it is stored with, and that source is in
  ``LINKEDIN_URL_SOURCES``. Every ``add_contact`` call in ``nextron/`` is in an allowlist and
  passes the source it names (only the callers whose candidate carries a ``linkedin_url`` need
  an allowed source; the runtime guard refuses the rest). Every call to the contacts
  repository's ``insert`` or ``fill`` passing ``linkedin_url`` or ``**`` is one of
  ``nextron.app.research.api``'s, so nothing writes the column around ``add_contact``'s guard.
- **Never fetched.** The guarantee is the FR-49 denylist every fetching adapter applies to each
  first URL and redirect (``tests/contracts/test_fetch_policy_contracts.py``): every URL
  ``normalize_linkedin_url`` returns is on it, so the egress rules refuse it. On top of that, a
  syntactic tripwire: no call to a public method of any ``Protocol`` in ``nextron.ports``, or to
  the adapters' ``fetch_resource`` and ``fetch_json``, takes an argument whose expression names
  LinkedIn or Instagram. It catches the obvious slip (``fetch_page(contact.linkedin_url)``), not
  a URL first bound to an unrelated name.

Each AST check has a negative probe on a synthetic module.
"""

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from nextron.domain.contacts import LINKEDIN_URL_SOURCES, ContactCandidate, ProvenanceSource
from nextron.domain.fetch_policy import DENYLISTED, is_denied, url_refusal
from nextron.domain.identity import LINKEDIN_PROFILE_PREFIX, normalize_linkedin_url
from tests.app.test_contactable import World, world  # noqa: F401 - the fixture
from tests.contracts.support import PACKAGE
from tests.contracts.test_no_linkedin_adapter import PLATFORM_WORD, _is_protocol_base, parse

ROOT: Final = PACKAGE.parent
ADAPTER_FETCH_METHODS: Final = frozenset({"fetch_resource", "fetch_json"})
# "<module>:<enclosing qualname>" -> the source its candidates are stored with
CANDIDATE_WITH_LINKEDIN_URL: Final = {
    "nextron.domain.lead_import:_Planner.contact": ProvenanceSource.CSV,
}
ADD_CONTACT_CALLERS: Final = {
    "nextron.app.discovery.api:LeadImports.run": ProvenanceSource.CSV,
    "nextron.app.research.people:PeopleExtractions._contacts": ProvenanceSource.WEBSITE,
}
CONTACTS_REPO: Final = "nextron.db.repos.research.contacts"
CONTACTS_REPO_WRITES: Final = frozenset({"insert", "fill"})
# "<module>:<enclosing qualname>" -> reason; only nextron.app.research.api
REPO_LINKEDIN_WRITERS: Final = {
    "nextron.app.research.api:Research.add_contact": "the guarded insert and match fill",
    "nextron.app.research.api:Research._resolve": "fills the Market and timezone columns only",
}
LINKEDIN_FIELD_POSITION: Final = 3  # ContactCandidate(name, role, email, linkedin_url, office)
PROFILE: Final = "https://www.linkedin.com/in/jane-doe"
DETAIL: Final = "Referred by a client, 2026-11"
T0: Final = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
# add_contact refuses eligibility_basis for every candidate (Story 3.7), so it isn't probed here
REFUSED_SOURCES: Final = (
    set(ProvenanceSource) - LINKEDIN_URL_SOURCES - {ProvenanceSource.ELIGIBILITY_BASIS}
)


# The AST checks ---------------------------------------------------------------------------------


def _modules(root: Path) -> list[tuple[str, ast.Module]]:
    found = []
    for path in sorted((root / "nextron").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(root).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        found.append((".".join(parts), parse(path)))
    return found


def _calls(module: str, tree: ast.Module) -> list[tuple[str, ast.Call]]:
    """Every call in ``tree`` with ``"<module>:<qualname of its enclosing def or class>"``."""
    found: list[tuple[str, ast.Call]] = []

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = (*scope, child.name)
            if isinstance(child, ast.Call):
                found.append((f"{module}:{'.'.join(scope)}", child))
            visit(child, inner)

    visit(tree, ())
    return found


def _callee(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _source(call: ast.Call) -> str | None:
    """The ``ProvenanceSource`` member an ``add_contact`` call passes as ``source``."""
    value = _keyword(call, "source")
    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
        if value.value.id == "ProvenanceSource":
            return value.attr
    return None


def _may_pass_linkedin_url(call: ast.Call, position: int | None) -> bool:
    """Whether ``call`` passes ``linkedin_url`` by keyword, through ``**``, through ``*`` or (when
    ``position`` is given) by that position."""
    if any(kw.arg in {"linkedin_url", None} for kw in call.keywords):
        return True
    if any(isinstance(arg, ast.Starred) for arg in call.args):
        return True
    return position is not None and len(call.args) > position


def candidate_problems(root: Path, allowed: dict[str, ProvenanceSource]) -> list[str]:
    """Each ``ContactCandidate`` (or ``replace``) that may carry a ``linkedin_url`` outside
    ``allowed``."""
    problems = []
    for module, tree in _modules(root):
        for where, call in _calls(module, tree):
            callee = _callee(call)
            if where in allowed:
                continue
            if callee == "ContactCandidate" and _may_pass_linkedin_url(
                call, LINKEDIN_FIELD_POSITION
            ):
                problems.append(f"{where}: a ContactCandidate with a linkedin_url")
            elif callee == "replace" and _may_pass_linkedin_url(call, None):
                problems.append(f"{where}: a linkedin_url replaced")
    return problems


def _repo_bindings(tree: ast.Module) -> tuple[set[str], set[str]]:
    """The names a module binds to the contacts repository, and to its write functions."""
    modules: set[str] = set()
    functions: set[str] = set()
    package, _, leaf = CONTACTS_REPO.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == package:
            modules |= {a.asname or a.name for a in node.names if a.name == leaf}
        elif isinstance(node, ast.ImportFrom) and node.module == CONTACTS_REPO:
            functions |= {
                a.asname or a.name for a in node.names if a.name in CONTACTS_REPO_WRITES
            }
        elif isinstance(node, ast.Import):
            modules |= {a.asname for a in node.names if a.name == CONTACTS_REPO and a.asname}
    return modules, functions


def repo_write_problems(root: Path, allowed: dict[str, str]) -> list[str]:
    """Each call to the contacts repository's ``insert`` or ``fill`` passing ``linkedin_url`` or
    ``**`` outside ``allowed``."""
    problems = []
    for module, tree in _modules(root):
        if module == CONTACTS_REPO:
            continue
        modules, functions = _repo_bindings(tree)
        for where, call in _calls(module, tree):
            func = call.func
            if isinstance(func, ast.Attribute) and func.attr in CONTACTS_REPO_WRITES:
                receiver = ast.unparse(func.value)
                hit = receiver in modules or receiver == CONTACTS_REPO
            else:
                hit = isinstance(func, ast.Name) and func.id in functions
            if hit and _may_pass_linkedin_url(call, None) and where not in allowed:
                problems.append(f"{where}: writes contacts.linkedin_url around add_contact")
    return problems


def add_contact_problems(root: Path, allowed: dict[str, ProvenanceSource]) -> list[str]:
    """Each ``add_contact`` call outside ``allowed`` or with another source than it names."""
    problems = []
    for module, tree in _modules(root):
        for where, call in _calls(module, tree):
            if _callee(call) != "add_contact":
                continue
            source = _source(call)
            if where not in allowed:
                problems.append(f"{where}: an add_contact caller not in the allowlist")
            elif source != allowed[where].name:
                problems.append(f"{where}: add_contact with source {source}, not the allowed one")
    return problems


def _fetch_methods(root: Path) -> frozenset[str]:
    """Every public method of every ``Protocol`` in ``nextron.ports`` (sync or async), and the
    adapters' own fetch methods."""
    methods = set(ADAPTER_FETCH_METHODS)
    for module, tree in _modules(root):
        if not module.startswith("nextron.ports"):
            continue
        for cls in ast.walk(tree):
            if isinstance(cls, ast.ClassDef) and any(map(_is_protocol_base, cls.bases)):
                methods |= {
                    node.name
                    for node in cls.body
                    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                    and not node.name.startswith("_")
                }
    return frozenset(methods)


def _names_a_platform(node: ast.AST) -> bool:
    for found in ast.walk(node):
        text = None
        if isinstance(found, ast.Name):
            text = found.id
        elif isinstance(found, ast.Attribute):
            text = found.attr
        elif isinstance(found, ast.Constant) and isinstance(found.value, str):
            text = found.value
        if text is not None and PLATFORM_WORD.search(text):
            return True
    return False


def fetch_problems(root: Path, methods: frozenset[str]) -> list[str]:
    problems = []
    for module, tree in _modules(root):
        for where, call in _calls(module, tree):
            if _callee(call) not in methods:
                continue
            arguments = [*call.args, *(kw.value for kw in call.keywords)]
            if any(_names_a_platform(argument) for argument in arguments):
                problems.append(f"{where} (l.{call.lineno}): {_callee(call)} on a platform URL")
    return problems


# The tree as committed --------------------------------------------------------------------------


def test_the_allowed_sources_are_pinned() -> None:
    assert frozenset(
        {
            ProvenanceSource.SIGNAL_INBOX,
            ProvenanceSource.WEBSITE,
            ProvenanceSource.CSV,
            ProvenanceSource.REFERRAL,
            ProvenanceSource.OPERATOR,
        }
    ) == LINKEDIN_URL_SOURCES


def test_every_linkedin_url_write_path_is_known_and_from_an_allowed_source() -> None:
    assert candidate_problems(ROOT, CANDIDATE_WITH_LINKEDIN_URL) == []
    assert add_contact_problems(ROOT, ADD_CONTACT_CALLERS) == []
    assert repo_write_problems(ROOT, REPO_LINKEDIN_WRITERS) == []
    assert set(CANDIDATE_WITH_LINKEDIN_URL.values()) <= LINKEDIN_URL_SOURCES
    assert all(where.startswith("nextron.app.research.api:") for where in REPO_LINKEDIN_WRITERS)


def test_every_allowlisted_write_path_still_exists() -> None:
    """A stale entry would let a new caller of that name in unseen."""
    calls = {where for module, tree in _modules(ROOT) for where, _ in _calls(module, tree)}
    assert CANDIDATE_WITH_LINKEDIN_URL.keys() <= calls
    assert ADD_CONTACT_CALLERS.keys() <= calls
    assert REPO_LINKEDIN_WRITERS.keys() <= calls


def test_no_fetch_takes_a_linkedin_url() -> None:
    methods = _fetch_methods(ROOT)
    assert {"fetch_page", "fetch_robots", "render", "postings", "fetch_resource"} <= methods
    assert {"fetch_json"} <= methods
    assert fetch_problems(ROOT, methods) == []


@pytest.mark.parametrize(
    "text",
    [
        "linkedin.com/in/jane-doe",
        "https://uk.linkedin.com/in/jane-doe/",
        "http://www.linkedin.com/in/Jane-Doe?trk=public_profile",
        "www.linkedin.com/in/jane-doe#about",
        "HTTPS://WWW.LINKEDIN.COM/IN/JANE-DOE",
    ],
)
def test_every_normalised_profile_url_is_denylisted(text: str) -> None:
    url = normalize_linkedin_url(text)

    assert url.startswith(LINKEDIN_PROFILE_PREFIX)
    assert is_denied(url)
    assert url_refusal(url) == DENYLISTED


# The guard in Research.add_contact --------------------------------------------------------------


def _add(world: World, source: ProvenanceSource, candidate: ContactCandidate) -> object:
    lead_id = world.lead()
    return world.transact(
        lambda: world.research.add_contact(
            lead_id, candidate, source=source, detail=DETAIL, retrieved_at=T0
        )
    )


def _counts(world: World) -> tuple[int, ...]:
    return tuple(
        world.sql(f"SELECT count(*) FROM {table}")[0][0]  # noqa: S608 - fixed names
        for table in ("contacts", "provenance_records", "events")
    )


@pytest.mark.parametrize("source", sorted(REFUSED_SOURCES))
def test_a_linkedin_url_from_another_source_is_refused_before_any_write(
    world: World,  # noqa: F811 - the fixture
    source: ProvenanceSource,
) -> None:
    lead_id = world.lead()
    before = _counts(world)

    with pytest.raises(ValueError, match=f"a LinkedIn URL never comes from {source.value}"):
        world.transact(
            lambda: world.research.add_contact(
                lead_id,
                ContactCandidate(name="Jane Doe", linkedin_url=PROFILE),
                source=source,
                detail=DETAIL,
                retrieved_at=T0,
            )
        )

    assert _counts(world) == before
    assert world.contact_rows() == []


def test_the_refused_sources_are_finder_pattern_and_replier() -> None:
    assert {ProvenanceSource.FINDER, ProvenanceSource.PATTERN, ProvenanceSource.REPLIER} == (
        REFUSED_SOURCES
    )


@pytest.mark.parametrize("source", sorted(LINKEDIN_URL_SOURCES))
def test_a_linkedin_url_from_an_allowed_source_is_stored_with_its_provenance(
    world: World,  # noqa: F811 - the fixture
    source: ProvenanceSource,
) -> None:
    added = _add(world, source, ContactCandidate(name="Jane Doe", linkedin_url=PROFILE))

    contact_id = getattr(added, "contact_id", None)
    assert contact_id is not None
    [(_, _, _, _, _, stored)] = world.contact_rows()
    assert stored == PROFILE
    assert world.sql(
        "SELECT subject, source_kind, source_detail FROM provenance_records WHERE contact_id = ?",
        (contact_id,),
    ) == [("contact", source.value, DETAIL)]


def test_a_refused_source_without_a_linkedin_url_is_still_stored(
    world: World,  # noqa: F811 - the fixture
) -> None:
    added = _add(world, ProvenanceSource.REPLIER, ContactCandidate(name="Jane Doe"))

    assert getattr(added, "contact_id", None) is not None


# Negative probes ------------------------------------------------------------------------------


def _plant(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


@pytest.mark.parametrize(
    "source",
    [
        "def guess():\n    return ContactCandidate(name='x', linkedin_url=u)\n",
        "def guess():\n    return ContactCandidate('x', None, None, u)\n",
        "def guess(c):\n    return replace(c, linkedin_url=u)\n",
        "def guess(row):\n    return ContactCandidate(**row)\n",
        "def guess(cells):\n    return ContactCandidate(*cells)\n",
        "def guess(c, changes):\n    return replace(c, **changes)\n",
    ],
)
def test_a_planted_candidate_with_a_linkedin_url_is_caught(tmp_path: Path, source: str) -> None:
    _plant(tmp_path, "nextron/app/research/finder.py", source)

    assert candidate_problems(tmp_path, CANDIDATE_WITH_LINKEDIN_URL) != []
    assert candidate_problems(
        tmp_path, {"nextron.app.research.finder:guess": ProvenanceSource.FINDER}
    ) == []


@pytest.mark.parametrize(
    "source",
    [
        "from nextron.db.repos.research import contacts as contacts_repo\n\n\n"
        "def go(c):\n    contacts_repo.fill(c, now, linkedin_url=u)\n",
        "from nextron.db.repos.research import contacts\n\n\n"
        "def go(c, values):\n    contacts.fill(c, now, **values)\n",
        "from nextron.db.repos.research.contacts import insert as put\n\n\n"
        "def go():\n    put(lead_id=1, linkedin_url=u)\n",
        "import nextron.db.repos.research.contacts as repo\n\n\n"
        "def go():\n    repo.insert(linkedin_url=u)\n",
    ],
)
def test_a_planted_repo_write_around_add_contact_is_caught(tmp_path: Path, source: str) -> None:
    _plant(tmp_path, "nextron/app/replies/people.py", source)

    assert repo_write_problems(tmp_path, REPO_LINKEDIN_WRITERS) == [
        "nextron.app.replies.people:go: writes contacts.linkedin_url around add_contact"
    ]
    assert repo_write_problems(tmp_path, {"nextron.app.replies.people:go": "probe"}) == []


def test_a_repo_write_without_a_linkedin_url_is_not_a_problem(tmp_path: Path) -> None:
    _plant(
        tmp_path,
        "nextron/app/replies/people.py",
        "from nextron.db.repos.research import contacts as contacts_repo\n\n\n"
        "def go(c):\n    contacts_repo.fill(c, now, role='x')\n",
    )

    assert repo_write_problems(tmp_path, {}) == []


def test_a_planted_add_contact_caller_or_a_changed_source_is_caught(tmp_path: Path) -> None:
    call = "def go(r):\n    r.add_contact(1, c, source=ProvenanceSource.{}, detail='x')\n"
    _plant(tmp_path, "nextron/app/replies/people.py", call.format("REPLIER"))

    assert add_contact_problems(tmp_path, {}) == [
        "nextron.app.replies.people:go: an add_contact caller not in the allowlist"
    ]
    assert add_contact_problems(
        tmp_path, {"nextron.app.replies.people:go": ProvenanceSource.CSV}
    ) == ["nextron.app.replies.people:go: add_contact with source REPLIER, not the allowed one"]


@pytest.mark.parametrize(
    "source",
    [
        "def go(crawler, contact):\n    crawler.fetch_page(contact.linkedin_url, site=None)\n",
        "def go(r, url):\n    r.render(url, site='www.linkedin.com', min_interval_s=1)\n",
        "def go(c, linkedin):\n    c.fetch_resource(f'{linkedin}/in/x')\n",
        "def go(c, u):\n    c.fetch_robots(instagram_profile(u))\n",
    ],
)
def test_a_planted_fetch_of_a_platform_url_is_caught(tmp_path: Path, source: str) -> None:
    _plant(tmp_path, "nextron/app/research/look.py", source)

    methods = frozenset({"fetch_page", "render", "fetch_resource", "fetch_robots"})
    [problem] = fetch_problems(tmp_path, methods)
    assert problem.startswith("nextron.app.research.look:go (l.2): ")
