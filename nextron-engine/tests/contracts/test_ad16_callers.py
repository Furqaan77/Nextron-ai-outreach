"""AD-16 callers (FR-50, FR-15, FR-63, AD-1, AD-16, NFR-36, Story 3.4): every path that creates
or reaches a Lead, Contact, Enrollment, Draft, Task or send calls
``compliance.assert_contactable`` before it writes, with probes proving each check catches a
violation.

- **The manifest.** ``MANIFEST`` lists every AD-16 caller as ``Caller(name, where, qualname,
  calls=None, inserts=frozenset())``: a def is that caller when its module's path under
  ``nextron/`` starts with one of ``where``, its dotted qualified name (``Class.method``, nested
  defs included) fullmatches the ``qualname`` regex and, with ``calls``, it calls that attribute
  name. ``inserts`` are the guarded tables the caller may insert into. An entry is *built* when
  the AST finds such a def; the patterns are loose (``*find*email*``, ``*verify*email*``,
  ``*enroll*``, ``*approve*``), so a later name like ``approve_draft`` can't skip forever.
  ``find_contact_email`` (Story 2.13) also covers every ``EmailFinding`` method;
  ``verify_contact_email`` (Story 2.14) is ``EmailVerification.verify_contact_email``. Not built
  yet: the Signal Inbox chain, replies, ``approval.approve``, the pipeline
  Tasks and call sheets and ``compliance.assert_sendable``: their stories add a scenario here
  when they build them. ``sequencing.enroll`` (Story 4.8) runs on the shipped seeds with
  Law-PrivateAI x US active and one Sending Inbox: an eligible US Contact's first two gate calls
  (``enroll``'s own and ``evaluate_eligibility``'s) come before its first write, the third
  (``drafting.request_steps``, Story 4.9) before its ``drafts`` insert, and ``Blocked`` writes
  nothing. ``drafting.request_*`` (Story 4.9: ``Drafting.request_steps``) asks for Step 2 of an
  Enrollment made with Step 1 only: its gate call comes before its one ``drafts`` insert, and
  ``Blocked`` adds no row. ``scoring.pick`` (Story 4.10: ``Selection.pick``) picks an eligible
  US Lead whose Contact is its Primary, with one warmed-up active inbox: ``enroll``'s three gate
  calls, the first two before the first write; ``Blocked`` adds only the day's
  ``daily_capacity`` row (or updates it) and no Enrollment. Story 10.7's
  ``pipeline.channel_permissions`` (``ChannelPermissions.record`` and ``update_inbound``) gates
  the Contact on ``whatsapp`` or ``instagram`` for ``follow_up`` before any write; Blocked, it
  stores nothing, not even the Evidence.
- **Scenarios.** A built entry without a scenario fails; an unbuilt one is skipped with "not
  built yet: <name>". Each scenario's cases (for ``add_contact``: a new Contact, and a stored
  Contact matched by name; one entry per ``CONTACT_SOURCES`` source, every Provenance source but
  ``eligibility_basis``, which ``add_contact`` refuses before the gate since Story 3.7) run on a
  temporary database at head with the real services, a recording fake of
  ``nextron.app.compliance.api.assert_contactable`` and a ``before_cursor_execute`` log of the
  write statements (``INSERT``, ``UPDATE``, ``DELETE``, ``REPLACE``) on that engine. With
  ``Allowed()``, the gate is called with the expected subjects (compared by value), channel and
  purpose, before the first write (all of them before it, except for the CSV import, which
  gates row by row), and once before each insert into a guarded table. With ``Blocked``, no
  table gains a row (jobs included) and no write runs, except the one ``contact_blocked``
  ``events`` insert of ``add_contact``. Story 2.10's people
  step (``research.people_extraction``: ``PeopleExtractions._contacts``, each decision-maker
  through ``add_contact(source=website)``) runs the real ``PeopleExtractions.extract`` on Story
  2.9's research world (``tests.app.test_signal_extraction.build_world``) on the same engine;
  blocked, it still records its run, its lawyer count and its selection job, but no Contact
  and no person Evidence. Story 2.13's ``EmailFinding.find_contact_email`` (a use case) runs in a
  unit of work, as the job runner runs it, on a named Contact without an address whose
  Lead's crawled page publishes ``jane.doe@<domain>``: allowed, it gates the Contact, then the
  found address; blocked, it writes only its ``email_search_blocked`` event and the Contact's
  search outcome. Story 2.14's ``EmailVerification.verify_contact_email`` runs the same way on
  the Primary Contact's ``guessed`` CSV address, with a scripted MX record and Hunter answering
  ``valid``: allowed, it gates the Contact before its first write and again before the Hunter
  call, and verifies the address;
  blocked, it records only the result ``suppressed`` (one ``UPDATE contacts``), makes no DNS or
  Hunter call and leaves the class ``guessed``.
- **Call sites.** Every use of ``upsert_lead`` or ``add_contact`` (a call or a reference) under
  ``nextron/`` sits lexically inside a manifest def, so a new caller (Epic 8 discovery, Epic 7
  replies) can't skip the manifest.
- **Update scan** (Story 2.13; closes Story 3.4's deferred item E). Only the contacts
  repository and manifest defs write ``contacts.email``: outside
  ``db/repos/research/contacts.py`` and every manifest def, any use of that module's
  ``set_address`` (a call or a reference, however the module or the function is imported), any
  call of its ``fill`` with an ``email`` keyword or a ``**`` splat, and any reference to
  ``fill`` that isn't the callee of a call (an alias could hide either) fails.
- **Insert scan.** Each function under ``db/repos`` that inserts into a guarded table
  (``leads``, ``contacts``, ``enrollments``, ``drafts``, ``linkedin_tasks``, ``call_preps``,
  ``outbound_messages``, found by the models' ``__tablename__``) through ``insert(Model)``,
  ``insert(Model.__table__)``, ``insert(table("leads"))``, ``Model(...)``,
  ``Model.__table__.insert()``, ``table("leads").insert()`` or ``bulk_insert_mappings`` /
  ``bulk_save_objects`` of a model may be used only in its own module or inside a manifest def
  that lists every table it inserts into. No string under ``nextron/`` (outside the migrations;
  docstrings skipped) holds ``INSERT INTO`` (or ``REPLACE INTO``) a guarded table, with or
  without a plain, quoted or bracketed schema.
"""

import ast
import importlib.util
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any, Final

import pytest
import sqlalchemy
from sqlalchemy import Engine, event

from nextron import main
from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.discovery.api import Discovery, ImportRequest, ImportSummary, LeadImports
from nextron.app.drafting.api import Drafting
from nextron.app.discovery.place_ingest import PlaceDiscovery
from nextron.app.discovery.places import PlaceSearches
from nextron.app.jobqueue.api import JobQueue
from nextron.app.ops.api import Seeds
from nextron.app.pipeline.api import Pipeline
from nextron.app.pipeline.channel_permissions import ChannelPermissions
from nextron.app.research.api import AddedContact, Research
from nextron.app.research.finding import EmailFinding
from nextron.app.research.people import PeopleExtractions
from nextron.app.research.verification import EmailVerification
from nextron.app.scoring.selection import Selection
from nextron.app.sending.api import Sending
from nextron.app.sequencing.api import Sequencing
from nextron.app.setup.api import Campaigns, Setup, check_seed
from nextron.db.engine import create_engine
from nextron.db.migrations import ensure_current
from nextron.db.repos.discovery import runs as runs_repo
from nextron.db.repos.research import contacts as contacts_repo
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.address_class import ASSIGNMENT
from nextron.domain.ai import Urgency
from nextron.domain.company_facts import validate_company_facts
from nextron.domain.contactability import (
    Allowed,
    Blocked,
    BlockReason,
    ContactSubject,
    NewContactSubject,
    NewLeadSubject,
)
from nextron.domain.channel_permission import PermissionRefused
from nextron.domain.contacts import LINKEDIN_URL_SOURCES, ContactCandidate, ProvenanceSource
from nextron.domain.crawl import CrawlOutcome, PageKind
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.campaign import validate_campaigns
from nextron.domain.enrollment import EnrollmentOrigin, NewEnrollment
from nextron.domain.markets import validate_markets
from nextron.domain.offers import validate_offers
from nextron.domain.place_discovery import DiscoveryLocation, RunStatus
from nextron.domain.segments import validate_segments
from nextron.domain.sequences import validate_sequences
from nextron.ports.dns import MxRecord
from nextron.ports.email_finder import VerifierAnswer
from nextron.ports.llm import LlmOk, LlmUsage
from nextron.ports.places import PlaceCandidate
from tests.app.test_signal_extraction import HAIKU as RESEARCH_MODEL
from tests.app.test_signal_extraction import HOME_HTML as RESEARCH_HOME_HTML
from tests.app.test_signal_extraction import build_world as research_world
from tests.contracts.support import PACKAGE
from tests.fakes.clock import FrozenClock
from tests.fakes.crawler import html_page, robots_txt
from tests.fakes.dns import FakeDns
from tests.fakes.email_finder import ScriptedDomainSearch, ScriptedFinder, ScriptedVerifier
from tests.fakes.email_syntax import FakeEmailSyntax
from tests.fakes.crawler import ScriptedCrawler
from tests.fakes.llm import ScriptedLlm
from tests.fakes.places import ScriptedPlaces
from tests.fakes.usage_meter import InMemoryUsageMeter

GUARDED_TABLES: Final = frozenset(
    {
        "leads",
        "contacts",
        "enrollments",
        "drafts",
        "linkedin_tasks",
        "call_preps",
        "outbound_messages",
    }
)
GATED_COMMANDS: Final = frozenset({"upsert_lead", "add_contact"})
# The sources add_contact accepts: eligibility_basis is refused before the gate (Story 3.7), and
# only Research.record_eligibility_basis writes such a record.
CONTACT_SOURCES: Final = tuple(
    source for source in ProvenanceSource if source is not ProvenanceSource.ELIGIBILITY_BASIS
)
MODELS: Final = "db/models/"
REPOS: Final = "db/repos/"
MIGRATIONS: Final = "db/alembic/versions/"
ANY: Final = r"(?:\w+\.)*"  # any enclosing classes and defs
BULK_INSERTS: Final = ("bulk_insert_mappings", "bulk_save_objects")
ALLOW_ALL: Final = "User-agent: *\nDisallow:\n"


@dataclass(frozen=True, slots=True)
class Caller:
    """One AD-16 caller; see the module docstring."""

    name: str
    where: tuple[str, ...]
    qualname: str
    calls: str | None = None
    inserts: frozenset[str] = frozenset()


MANIFEST: Final = (
    Caller(
        "discovery.upsert_lead",
        ("app/discovery/",),
        r"Discovery\.upsert_lead",
        inserts=frozenset({"leads"}),
    ),
    Caller("discovery.csv_import", ("app/discovery/",), r"LeadImports\.run"),
    # Story 8.3: each Places candidate through upsert_lead(places)
    Caller(
        "discovery.places_ingest",
        ("app/discovery/",),
        r"PlaceDiscovery\.ingest_candidate",
        calls="upsert_lead",
    ),
    *(
        Caller(
            f"research.add_contact[{source}]",
            ("app/research/",),
            r"Research\.add_contact",
            inserts=frozenset({"contacts"}),
        )
        for source in CONTACT_SOURCES
    ),
    # Story 2.10's people step: each decision-maker on a team page through add_contact(website)
    Caller(
        "research.people_extraction",
        ("app/research/",),
        r"PeopleExtractions\._contacts",
        calls="add_contact",
    ),
    # Story 2.13: the job's EmailFinding, every method of which may write contacts.email
    Caller(
        "find_contact_email",
        ("app/",),
        ANY + r"(?:\w*find\w*email\w*|EmailFinding\.\w+)",
    ),
    Caller("verify_contact_email", ("app/",), ANY + r"\w*verify\w*email\w*"),
    Caller("signal_inbox", ("app/",), ANY + r"(?:process|ingest)_signal_item"),
    Caller("replies.add_contact", ("app/replies/",), ANY + r"\w+", calls="add_contact"),
    Caller(
        "sequencing.enroll",
        ("app/sequencing/",),
        ANY + r"\w*enroll\w*",
        inserts=frozenset({"enrollments"}),
    ),
    Caller(
        "drafting.request_*",
        ("app/drafting/",),
        ANY + r"request_\w+",
        inserts=frozenset({"drafts", "call_preps"}),
    ),
    Caller("approval.approve", ("app/approval/",), ANY + r"\w*approve\w*"),
    # Story 4.10: the Operator's picks enroll through sequencing.enroll
    Caller("scoring.pick", ("app/scoring/",), r"Selection\.pick"),
    Caller(
        "pipeline.tasks_and_call_sheets",
        ("app/pipeline/",),
        ANY + r"(?!cancel_)\w*(?:task|call_sheet)\w*",
        inserts=frozenset({"linkedin_tasks"}),
    ),
    # Story 10.7: recording a Channel Permission and moving an Instagram one's last inbound
    Caller(
        "pipeline.channel_permissions",
        ("app/pipeline/",),
        r"ChannelPermissions\.(?:record|update_inbound)",
    ),
    Caller(
        "compliance.assert_sendable",
        ("app/compliance/",),
        ANY + "assert_sendable",
        inserts=frozenset({"outbound_messages"}),
    ),
)
NAMES: Final = tuple(caller.name for caller in MANIFEST)


# The AST index ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Def:
    """One def: its module's path under ``nextron/`` and its dotted qualified name."""

    path: str
    qualname: str
    node: ast.FunctionDef | ast.AsyncFunctionDef = field(compare=False, repr=False)


def _modules(root: Path) -> Iterator[tuple[str, Path, ast.Module]]:
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        yield relative, path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _package_of(path: Path, root: Path) -> str:
    """The dotted package a module at ``path`` under the ``nextron`` folder ``root`` is in."""
    parts = path.relative_to(root.parent).with_suffix("").parts
    return ".".join(parts[:-1])


def _module_name(relative: str) -> str:
    return "nextron." + relative.removesuffix(".py").replace("/", ".")


def _imported_from(node: ast.ImportFrom, package: str) -> str:
    if not node.level:
        return node.module or ""
    base = package.split(".")[: len(package.split(".")) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return None if inner is None else f"{inner}.{node.attr}"
    return None


def _callee(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


class _Scopes(ast.NodeVisitor):
    """Walks a module keeping the chain of enclosing class and def names; ``seen`` gets every
    def and every ``Name``/``Attribute`` load with that chain."""

    def __init__(self, relative: str) -> None:
        self.relative = relative
        self.stack: list[str] = []
        self.defs: list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
        self.chains: list[tuple[str, ...]] = []  # the def qualnames around the current node
        self.names: list[tuple[int, str, tuple[str, ...], ast.expr]] = []

    def _class(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _def(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        qualname = ".".join(self.stack)
        self.defs.append((self.relative, qualname, node))
        self.chains.append((*(self.chains[-1] if self.chains else ()), qualname))
        self.generic_visit(node)
        self.chains.pop()
        self.stack.pop()

    visit_ClassDef = _class
    visit_FunctionDef = _def
    visit_AsyncFunctionDef = _def

    def _chain(self) -> tuple[str, ...]:
        return self.chains[-1] if self.chains else ()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            self.names.append((node.lineno, node.attr, self._chain(), node))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.names.append((node.lineno, node.id, self._chain(), node))


@dataclass(frozen=True, slots=True)
class Index:
    """Every def and every name use under one ``nextron`` folder."""

    root: Path
    defs: tuple[Def, ...]
    names: tuple[tuple[str, int, str, tuple[str, ...], ast.expr], ...]
    trees: Mapping[str, tuple[Path, ast.Module]]


def build_index(root: Path) -> Index:
    defs: list[Def] = []
    names: list[tuple[str, int, str, tuple[str, ...], ast.expr]] = []
    trees: dict[str, tuple[Path, ast.Module]] = {}
    for relative, path, tree in _modules(root):
        trees[relative] = (path, tree)
        scopes = _Scopes(relative)
        scopes.visit(tree)
        defs += [Def(where, qualname, node) for where, qualname, node in scopes.defs]
        names += [(relative, line, name, chain, node) for line, name, chain, node in scopes.names]
    return Index(root, tuple(defs), tuple(names), MappingProxyType(trees))


def _calls(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(found, ast.Call) and _callee(found.func) == name for found in ast.walk(node)
    )


def is_caller(caller: Caller, found: Def) -> bool:
    return (
        found.path.startswith(caller.where)
        and re.fullmatch(caller.qualname, found.qualname) is not None
        and (caller.calls is None or _calls(found.node, caller.calls))
    )


def built(index: Index, manifest: Iterable[Caller] = MANIFEST) -> dict[str, tuple[Def, ...]]:
    """Each manifest entry's defs; an empty tuple when it isn't built yet."""
    return {
        caller.name: tuple(found for found in index.defs if is_caller(caller, found))
        for caller in manifest
    }


def _manifest_tables(index: Index, manifest: Iterable[Caller]) -> dict[tuple[str, str], set[str]]:
    """Each manifest def, by (path, qualname), with the guarded tables it may insert into."""
    tables: dict[tuple[str, str], set[str]] = {}
    for caller in manifest:
        for found in index.defs:
            if is_caller(caller, found):
                tables.setdefault((found.path, found.qualname), set()).update(caller.inserts)
    return tables


def _inside(
    path: str, chain: tuple[str, ...], allowed: Mapping[tuple[str, str], set[str]]
) -> list[set[str]]:
    """The table sets of the manifest defs lexically around a use; empty outside them all."""
    return [allowed[(path, qualname)] for qualname in chain if (path, qualname) in allowed]


@cache
def package_index() -> Index:
    return build_index(PACKAGE)


# Scenario coverage ------------------------------------------------------------------------------


def scenario_problems(
    index: Index, scenarios: Iterable[str], manifest: Iterable[Caller] = MANIFEST
) -> list[str]:
    """Each built manifest entry without a scenario."""
    covered = set(scenarios)
    return [
        f"{name} is built ({found[0].path} {found[0].qualname}) but has no scenario"
        for name, found in built(index, manifest).items()
        if found and name not in covered
    ]


# Call sites -------------------------------------------------------------------------------------


def call_site_problems(index: Index, manifest: Iterable[Caller] = MANIFEST) -> list[str]:
    """Each use of ``upsert_lead`` or ``add_contact`` outside every manifest def."""
    allowed = _manifest_tables(index, manifest)
    return [
        f"{path}:{line} {'.'.join(chain[-1:]) or '<module>'} uses {name} outside the AD-16 "
        "manifest"
        for path, line, name, chain, _ in index.names
        if name in GATED_COMMANDS and not _inside(path, chain, allowed)
    ]


# The insert scan --------------------------------------------------------------------------------


def guarded_models(index: Index) -> dict[str, str]:
    """Each model class under ``db/models`` whose ``__tablename__`` is a guarded table."""
    models: dict[str, str] = {}
    for relative, (_, tree) in index.trees.items():
        if not relative.startswith(MODELS):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                targets: list[ast.expr] = []
                value: ast.expr | None = None
                if isinstance(statement, ast.Assign):
                    targets, value = statement.targets, statement.value
                elif isinstance(statement, ast.AnnAssign):
                    targets, value = [statement.target], statement.value
                if (
                    any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in targets)
                    and isinstance(value, ast.Constant)
                    and value.value in GUARDED_TABLES
                ):
                    models[node.name] = str(value.value)
    return models


def _model_of(node: ast.expr, models: Mapping[str, str], aliases: Mapping[str, str]) -> str | None:
    """The guarded table ``node`` names as a model (a name, an alias or an attribute), as a
    model's ``__table__``, or as ``table("<guarded table>")`` / ``sa.table(...)``."""
    if isinstance(node, ast.Name):
        return aliases.get(node.id, models.get(node.id))
    if isinstance(node, ast.Attribute):
        if node.attr == "__table__":
            return _model_of(node.value, models, aliases)
        return models.get(node.attr)
    if (
        isinstance(node, ast.Call)
        and _callee(node.func) == "table"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in GUARDED_TABLES
    ):
        return str(node.args[0].value)
    return None


def _inserted_table(
    call: ast.Call, models: Mapping[str, str], aliases: Mapping[str, str], inserts: set[str]
) -> str | None:
    """The guarded table one call inserts into, or ``None``."""
    func = call.func
    if isinstance(func, ast.Name | ast.Attribute) and getattr(func, "attr", "") != "__table__":
        constructed = _model_of(func, models, aliases)
        if constructed is not None:  # Model(...)
            return constructed
    named_insert = (isinstance(func, ast.Name) and func.id in inserts) or (
        isinstance(func, ast.Attribute) and func.attr in ("insert", *BULK_INSERTS)
    )
    if not named_insert:
        return None
    if call.args:  # insert(Model), insert(Model.__table__), insert(table("leads")), bulk_*(Model)
        found = _model_of(call.args[0], models, aliases)
        if found is not None:
            return found
    if isinstance(func, ast.Attribute) and func.attr == "insert":  # Model.__table__.insert()
        return _model_of(func.value, models, aliases)  # or table("leads").insert()
    return None


def inserting_functions(index: Index) -> dict[tuple[str, str], frozenset[str]]:
    """Each top-level function under ``db/repos`` that inserts into a guarded table, by (path,
    name), with those tables; a function calling another of its module's inserting functions
    inserts too."""
    models = guarded_models(index)
    found: dict[tuple[str, str], frozenset[str]] = {}
    for relative, (_, tree) in index.trees.items():
        if not relative.startswith(REPOS):
            continue
        aliases: dict[str, str] = {}
        inserts: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if alias.name in models:
                        aliases[bound] = models[alias.name]
                    elif alias.name == "insert" and (node.module or "").startswith("sqlalchemy"):
                        inserts.add(bound)
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        tables: dict[str, set[str]] = {
            name: {
                table
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and (table := _inserted_table(call, models, aliases, inserts)) is not None
            }
            for name, node in functions.items()
        }
        callees = {
            name: {
                callee
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and (callee := call.func.id) in functions
            }
            for name, node in functions.items()
        }
        changed = True
        while changed:  # a wrapper of an inserting function inserts too
            changed = False
            for name, called in callees.items():
                for callee in called:
                    if not tables[callee] <= tables[name]:
                        tables[name] |= tables[callee]
                        changed = True
        for name, inserted in tables.items():
            if inserted:
                found[(relative, name)] = frozenset(inserted)
    return found


def _bindings(
    tree: ast.Module, package: str, module: str, functions: Iterable[str]
) -> tuple[set[str], dict[str, str]]:
    """The names bound to ``module`` (dotted) and to each of its ``functions`` in one module."""
    parent, _, leaf = module.rpartition(".")
    grandparent, _, middle = parent.rpartition(".")
    wanted = set(functions)
    modules: set[str] = set()
    bound_functions: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module:
                    modules.add(alias.asname or alias.name)
                elif alias.name == parent:
                    modules.add(f"{alias.asname or alias.name}.{leaf}")
        elif isinstance(node, ast.ImportFrom):
            source = _imported_from(node, package)
            for alias in node.names:
                bound = alias.asname or alias.name
                if source == parent and alias.name == leaf:
                    modules.add(bound)
                elif source == grandparent and alias.name == middle:
                    modules.add(f"{bound}.{leaf}")
                elif source == module and alias.name in wanted:
                    bound_functions[bound] = alias.name
    return modules, bound_functions


def insert_problems(index: Index, manifest: Iterable[Caller] = MANIFEST) -> list[str]:
    """Each use of a guarded-insert function outside its module and outside every manifest def
    that lists its tables."""
    inserting = inserting_functions(index)
    allowed = _manifest_tables(index, manifest)
    by_module: dict[str, dict[str, frozenset[str]]] = {}
    for (relative, name), tables in inserting.items():
        by_module.setdefault(relative, {})[name] = tables
    uses_by_path: dict[str, list[tuple[int, str, tuple[str, ...], ast.expr]]] = {}
    for path, line, name, chain, node in index.names:
        uses_by_path.setdefault(path, []).append((line, name, chain, node))
    problems = []
    for relative, (path, tree) in index.trees.items():
        package = _package_of(path, index.root)
        for repo, functions in by_module.items():
            if relative == repo:
                continue
            modules, bound = _bindings(tree, package, _module_name(repo), functions)
            if not (modules or bound):
                continue
            for line, name, chain, node in uses_by_path.get(relative, []):
                if isinstance(node, ast.Attribute) and node.attr in functions:
                    if _dotted(node.value) not in modules:
                        continue
                    function = node.attr
                elif isinstance(node, ast.Name) and node.id in bound:
                    function = bound[node.id]
                else:
                    continue
                tables = functions[function]
                if any(tables <= listed for listed in _inside(relative, chain, allowed)):
                    continue
                scope = chain[-1] if chain else "<module>"
                problems.append(
                    f"{relative}:{line} {scope} uses {repo} {function}, which inserts into "
                    f"{', '.join(sorted(tables))}, outside an AD-16 manifest def listing it"
                )
    return sorted(set(problems))


SQL_INSERT: Final = re.compile(
    r"\b(?:insert\s+(?:or\s+\w+\s+)?into|replace\s+into)\s+"
    r"(?:[\"'`\[]?\w+[\"'`\]]?\s*\.\s*)?[\"'`\[]?("
    + "|".join(sorted(GUARDED_TABLES))
    + r")\b",
    re.IGNORECASE,
)


def _docstrings(tree: ast.Module) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return found


def raw_sql_problems(index: Index) -> list[str]:
    """Each string (not a docstring) outside the migrations that inserts into a guarded table."""
    problems = []
    for relative, (_, tree) in index.trees.items():
        if relative.startswith(MIGRATIONS):
            continue
        docstrings = _docstrings(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and (match := SQL_INSERT.search(node.value)) is not None
            ):
                problems.append(f"{relative}:{node.lineno} holds INSERT INTO {match.group(1)}")
    return sorted(set(problems))


# The update scan --------------------------------------------------------------------------------

CONTACTS_REPO: Final = "db/repos/research/contacts.py"
SET_ADDRESS: Final = "set_address"
FILL: Final = "fill"


def _sets_email(call: ast.Call) -> bool:
    """Whether a ``fill`` call names ``email`` or splats ``**`` keywords."""
    return any(keyword.arg in (None, "email") for keyword in call.keywords)


def update_problems(index: Index, manifest: Iterable[Caller] = MANIFEST) -> list[str]:
    """Each use of the contacts repository's ``set_address``, ``fill`` call with ``email`` or
    ``**``, or reference to ``fill`` that isn't a call's callee, outside that module and every
    manifest def."""
    allowed = _manifest_tables(index, manifest)
    module = _module_name(CONTACTS_REPO)
    uses_by_path: dict[str, list[tuple[int, str, tuple[str, ...], ast.expr]]] = {}
    for path, line, name, chain, node in index.names:
        uses_by_path.setdefault(path, []).append((line, name, chain, node))
    problems = []
    for relative, (path, tree) in index.trees.items():
        if relative == CONTACTS_REPO:
            continue
        package = _package_of(path, index.root)
        modules, bound = _bindings(tree, package, module, (SET_ADDRESS, FILL))
        if not (modules or bound):
            continue
        calls = {id(node.func): node for node in ast.walk(tree) if isinstance(node, ast.Call)}
        for line, _, chain, node in uses_by_path.get(relative, []):
            if isinstance(node, ast.Attribute) and node.attr in (SET_ADDRESS, FILL):
                if _dotted(node.value) not in modules:
                    continue
                function = node.attr
            elif isinstance(node, ast.Name) and node.id in bound:
                function = bound[node.id]
            else:
                continue
            if _inside(relative, chain, allowed):
                continue
            call = calls.get(id(node))
            if function == SET_ADDRESS:
                what = "uses set_address"
            elif call is None:
                what = "references fill outside a call"
            elif _sets_email(call):
                what = "calls fill with email or **"
            else:
                continue
            scope = chain[-1] if chain else "<module>"
            problems.append(
                f"{relative}:{line} {scope} {what} of {CONTACTS_REPO} outside the AD-16 manifest"
            )
    return sorted(set(problems))


# The recording harness --------------------------------------------------------------------------

WRITE: Final = re.compile(
    r"^\s*(insert(?:\s+or\s+\w+)?\s+into|replace\s+into|update(?:\s+or\s+\w+)?|delete\s+from)"
    r"\s+[\"'`\[]?(\w+)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class GateCall:
    subject: object
    channel: str
    purpose: str


@dataclass(frozen=True, slots=True)
class Write:
    verb: str  # INSERT, UPDATE, DELETE or REPLACE
    table: str


class Recorder:
    """The gate's recording fake and the engine's write log, in one ordered list."""

    def __init__(self, answer: Allowed | Blocked) -> None:
        self.answer = answer
        self.log: list[GateCall | Write] = []

    def gate(self, subject: object, channel: object, purpose: object) -> Allowed | Blocked:
        self.log.append(GateCall(subject, str(channel), str(purpose)))
        return self.answer

    def statement(self, *args: Any) -> None:
        statement = args[2]
        match = WRITE.match(statement)
        if match is not None:
            verb = match.group(1).split()[0].upper()
            self.log.append(Write(verb, match.group(2).lower()))

    @property
    def calls(self) -> list[GateCall]:
        return [entry for entry in self.log if isinstance(entry, GateCall)]


@contextmanager
def recording(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, answer: Allowed | Blocked
) -> Iterator[Recorder]:
    """The gate replaced by a recording fake answering ``answer``, and ``engine``'s writes
    logged, until the block ends."""
    recorder = Recorder(answer)
    with monkeypatch.context() as patch:
        patch.setattr(compliance_api, "assert_contactable", recorder.gate)
        event.listen(engine, "before_cursor_execute", recorder.statement)
        try:
            yield recorder
        finally:
            event.remove(engine, "before_cursor_execute", recorder.statement)


def order_problems(log: Iterable[GateCall | Write]) -> list[str]:
    """A write before the first gate call, and each guarded insert without a gate call of its
    own (one since the previous guarded insert)."""
    problems = []
    gated = seen_gate = False
    for entry in log:
        if isinstance(entry, GateCall):
            gated = seen_gate = True
            continue
        if not seen_gate:
            problems.append(f"{entry.verb} {entry.table} comes before the gate call")
            seen_gate = True  # reported once
        if entry.verb in ("INSERT", "REPLACE") and entry.table in GUARDED_TABLES:
            if not gated:
                problems.append(f"{entry.verb} {entry.table} has no gate call of its own")
            gated = False
    return problems


def table_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        tables = [
            str(name)
            for (name,) in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).all()
        ]
        return {
            table: int(connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{table}"').scalar_one())
            for table in tables
        }


def gained_rows(
    before: Mapping[str, int], after: Mapping[str, int], allowed: Mapping[str, int]
) -> list[str]:
    """Each table that gained more rows than ``allowed`` gives it."""
    return [
        f"{table} gained {after[table] - before.get(table, 0)} row(s)"
        for table in sorted(after)
        if after[table] - before.get(table, 0) > allowed.get(table, 0)
    ]


def blocked_write_problems(
    log: Iterable[GateCall | Write], allowed: frozenset[tuple[str, str]]
) -> list[str]:
    """Each write of a ``Blocked`` run that isn't one of the ``allowed`` (verb, table)."""
    return [
        f"{entry.verb} {entry.table} after a Blocked answer"
        for entry in log
        if isinstance(entry, Write) and (entry.verb, entry.table) not in allowed
    ]


def gates_first_problems(log: Iterable[GateCall | Write]) -> list[str]:
    """A gate call after the first write (for a caller that gates once per unit)."""
    entries = list(log)
    writes = [index for index, entry in enumerate(entries) if isinstance(entry, Write)]
    gates = [index for index, entry in enumerate(entries) if isinstance(entry, GateCall)]
    if not writes or not gates or gates[-1] < writes[0]:
        return []
    first = entries[writes[0]]
    assert isinstance(first, Write)
    return [f"a gate call comes after {first.verb} {first.table}"]


def leading_gates_problems(log: Iterable[GateCall | Write], count: int) -> list[str]:
    """Fewer than ``count`` gate calls before the first write (Story 4.8's enroll: its own
    gate call and ``evaluate_eligibility``'s both come before any write)."""
    leading = 0
    for entry in log:
        if isinstance(entry, Write):
            break
        leading += 1
    if leading >= count:
        return []
    return [f"only {leading} of {count} gate calls come before the first write"]


# The scenarios ----------------------------------------------------------------------------------

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
SHIPPED_MARKETS = main.SEED_DIR / main.MARKETS_FILE_NAME
BLOCKED = Blocked((BlockReason.LEAD_DOMAIN_SUPPRESSED,))
EMAIL = "email"
DISCOVERY = "discovery"
NAME = "Jane Doe"


def no_jobs() -> Any:
    """``build_job_types``'s services: no job runs in these tests."""
    raise AssertionError("no job runs in these tests")


def queue_on(clock: FrozenClock) -> JobQueue:
    """A job queue on the real job types; ``upsert_lead`` enqueues a CSV Lead's crawl there."""
    job_types = main.build_job_types(
        alerts=no_jobs,
        backups=no_jobs,
        restore_tests=no_jobs,
        recovery=no_jobs,
        contact_markets=no_jobs,
        crawls=no_jobs,
        discovery=no_jobs,
        research=no_jobs,
        extractions=no_jobs,
        primary=no_jobs,
        test_runs=no_jobs,
        job_boards=no_jobs,
        legal_forms=no_jobs,
        scores=no_jobs,
    )
    return JobQueue(clock, job_types)


@dataclass
class World:
    """The real services on a temporary database at head, with Segments and Markets seeded."""

    engine: Engine
    units: UnitOfWorkFactory
    discovery: Discovery
    research: Research
    imports: LeadImports
    segment: str

    def transact[R](self, fn: Callable[[], R]) -> R:
        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def lead(self, domain: str) -> str:
        identity = LeadIdentity("Firm LLP", self.segment, web_domain=domain, country_code="GB")
        upserted = self.transact(lambda: self.discovery.upsert_lead(identity, LeadSource.CSV))
        assert upserted.lead_id is not None
        return upserted.lead_id

    def adder(
        self, lead_id: str, candidate: ContactCandidate, source: ProvenanceSource
    ) -> Callable[[], AddedContact]:
        return lambda: self.transact(
            lambda: self.research.add_contact(
                lead_id, candidate, source=source, detail="AD-16 contract", retrieved_at=T0
            )
        )

    def contact(self, lead_id: str, candidate: ContactCandidate) -> str:
        contact_id = self.adder(lead_id, candidate, ProvenanceSource.CSV)().contact_id
        assert contact_id is not None
        return contact_id

    def lead_id_of(self, domain: str) -> str:
        with self.engine.connect() as connection:
            return str(
                connection.exec_driver_sql(
                    "SELECT id FROM leads WHERE web_domain = ?", (domain,)
                ).scalar_one()
            )

    def run(self, data: bytes) -> ImportSummary:
        with self.units.open():
            return self.imports.run(ImportRequest("AD-16 contract list", "law_private_ai", data))

    def last_event(self) -> tuple[Any, ...]:
        with self.engine.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT entity, from_status, to_status, cause, actor FROM events "
                "ORDER BY rowid DESC LIMIT 1"
            ).one()
        return tuple(row)


@contextmanager
def world_at(folder: Path) -> Iterator[World]:
    folder.mkdir(parents=True)
    engine = create_engine(folder / "nextron.db")
    try:
        ensure_current(engine)
        clock = FrozenClock(T0)
        units = UnitOfWorkFactory(engine, main.build_subscribers(clock=clock))
        segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
        markets = validate_markets(seed_yaml.parse(SHIPPED_MARKETS.read_text(encoding="utf-8")))
        with units.open():
            Setup(clock).sync_segments(segments)
        with units.open():
            Setup(clock).sync_markets(markets)
        discovery = Discovery(clock, Pipeline(clock), queue_on(clock))
        research = Research(clock)
        imports = LeadImports(clock, discovery, research, FakeEmailSyntax(), queue_on(clock))
        with engine.connect() as connection:
            segment = str(
                connection.exec_driver_sql("SELECT id FROM segments ORDER BY key").first()[0]
            )
        yield World(engine, units, discovery, research, imports, segment)
    finally:
        engine.dispose()


type Expect = Callable[[], tuple[GateCall, ...]]
type Prepare = Callable[[World, str], tuple[Callable[[], object], Expect]]


@dataclass(frozen=True, slots=True)
class Scenario:
    """How to run one caller. Each of ``cases`` is a ``prepare(world, tag)`` that stores what
    it needs (unrecorded) and returns the action to record and, called after it, the gate calls
    the action must make with ``Allowed``, compared by value. ``gates_first``: every gate call
    comes before the first write (one unit of work per action; not the CSV import, which gates
    row by row). ``gates_before_first_write``: with ``gates_first`` off, how many gate calls must
    still come before the first write. With ``Blocked``: the rows a run may still add (``blocked_rows``), the writes
    it may still make, as (verb, table) (``blocked_writes``), and a check of them
    (``after_blocked``)."""

    cases: tuple[Prepare, ...]
    gates_first: bool = True
    gates_before_first_write: int = 0
    blocked_rows: Mapping[str, int] = field(default_factory=dict)
    blocked_writes: frozenset[tuple[str, str]] = frozenset()
    after_blocked: Callable[[World], None] | None = None


def _gate(subject: object) -> GateCall:
    return GateCall(subject, EMAIL, DISCOVERY)


def _upsert(world: World, tag: str) -> tuple[Callable[[], object], Expect]:
    domain = f"firm-{tag}.co.uk"
    identity = LeadIdentity(f"Firm {tag}", world.segment, web_domain=domain, country_code="GB")

    def action() -> object:
        return world.transact(lambda: world.discovery.upsert_lead(identity, LeadSource.CSV))

    return action, lambda: (_gate(NewLeadSubject(domain)),)


def _csv(world: World, tag: str) -> tuple[Callable[[], object], Expect]:
    domain = f"firm-{tag}.co.uk"
    email = f"jane@{domain}"
    data = (
        "Organization name,Website,Country,Contact name,Email\r\n"
        f"Firm {tag},{domain},GB,{NAME},{email}\r\n"
    ).encode()

    def expected() -> tuple[GateCall, ...]:  # the Lead's id exists only after the run
        lead_id = world.lead_id_of(domain)
        return (
            _gate(NewLeadSubject(domain)),
            _gate(NewContactSubject(lead_id, email=email, name=NAME)),
        )

    return lambda: world.run(data), expected


def _add_new(source: ProvenanceSource) -> Prepare:
    """A new Contact: one gate call with the candidate's values."""

    def prepare(world: World, tag: str) -> tuple[Callable[[], object], Expect]:
        lead_id = world.lead(f"firm-{tag}.co.uk")
        email = f"jane@firm-{tag}.co.uk" if source in ASSIGNMENT else None
        candidate = ContactCandidate(name=NAME, email=email)
        expected = (_gate(NewContactSubject(lead_id, email=email, name=NAME)),)
        return world.adder(lead_id, candidate, source), lambda: expected

    return prepare


def _add_match(source: ProvenanceSource) -> Prepare:
    """A stored Contact matched by name that the candidate's LinkedIn URL would fill (its role
    instead from a source a LinkedIn URL never comes from, Story 10.1): the candidate's values,
    then the match's own (decision 13)."""

    def prepare(world: World, tag: str) -> tuple[Callable[[], object], Expect]:
        lead_id = world.lead(f"firm-{tag}.co.uk")
        stored_id = world.contact(lead_id, ContactCandidate(name=NAME))
        if source in LINKEDIN_URL_SOURCES:
            linkedin = f"https://www.linkedin.com/in/jane-{tag}"
            candidate = ContactCandidate(name=NAME, linkedin_url=linkedin)
            first = NewContactSubject(lead_id, name=NAME, linkedin_url=linkedin)
        else:
            candidate = ContactCandidate(name=NAME, role="Managing Partner")
            first = NewContactSubject(lead_id, name=NAME)
        expected = (_gate(first), _gate(ContactSubject(stored_id)))
        return world.adder(lead_id, candidate, source), lambda: expected

    return prepare


def _contact_blocked(world: World) -> None:
    assert world.last_event() == (
        "leads",
        None,
        "contact_blocked",
        str(BLOCKED.reasons[0]),
        "system:research",
    )


PERSON_QUOTE = f"{NAME}, Managing Partner"


def _people(world: World, tag: str) -> tuple[Callable[[], object], Expect]:
    """Story 2.10's people step (``PeopleExtractions.extract``) on a Law Lead whose crawled team
    page names one decision-maker: one gate call, through ``add_contact(source=website)``.
    Story 2.9's research world (a scripted crawler and the shipped prompts) shares the engine."""
    site = research_world(world.engine)
    origin = f"https://firm-{tag}.com"
    site.crawler.robots[f"{origin}/robots.txt"] = robots_txt(
        f"{origin}/robots.txt", "User-agent: *\nDisallow:\n"
    )
    site.crawler.pages[f"{origin}/"] = html_page(f"{origin}/", RESEARCH_HOME_HTML)
    site.crawler.pages[f"{origin}/team"] = html_page(f"{origin}/team", f"<p>{PERSON_QUOTE}</p>")
    for missing in ("about", "news"):
        site.crawler.pages[f"{origin}/{missing}"] = html_page(f"{origin}/{missing}", status=404)
    lead_id = site.lead(web_domain=f"firm-{tag}.com")
    run_id = site.crawl(lead_id)
    team = site.page_ids(lead_id)[f"{origin}/team"]
    answer = LlmOk(
        {
            "people": [
                {"name": NAME, "role": "Managing Partner", "block_id": team, "quote": PERSON_QUOTE}
            ]
        },
        model=RESEARCH_MODEL,
        usage=LlmUsage(requests=1),
    )
    step = PeopleExtractions(site.clock, site.meter, ScriptedLlm(answer), site.research, site.queue)

    @use_case
    def extract_people() -> bool:  # as extract_lead runs it: inside a use case
        return step.extract(lead_id, run_id)

    def action() -> object:
        with site.units.open():
            return extract_people()

    return action, lambda: (_gate(NewContactSubject(lead_id, name=NAME)),)


def _no_person_evidence_without_a_contact(world: World) -> None:
    """A blocked decision-maker leaves no person Evidence (Story 2.10's deferred entry O) and
    one ``contact_blocked`` row."""
    with world.engine.connect() as connection:
        orphans = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM evidence WHERE person_contact_id IS NULL "
            "AND extracted_by LIKE 'ai:extract_people@%'"
        ).scalar_one()
        blocked = connection.exec_driver_sql(
            "SELECT entity, from_status, to_status, cause, actor FROM events "
            "WHERE to_status = 'contact_blocked' ORDER BY rowid DESC LIMIT 1"
        ).one()
    assert orphans == 0
    assert tuple(blocked) == (
        "leads",
        None,
        "contact_blocked",
        str(BLOCKED.reasons[0]),
        "system:research",
    )


def _shipped[T](name: str, validate: Callable[[object], T]) -> T:
    return validate(seed_yaml.parse((main.SEED_DIR / name).read_text(encoding="utf-8")))


def _enrollable(world: World) -> str:
    """The shipped seeds synced, Law-PrivateAI x US active and one Sending Inbox registered
    (once per world); the Campaign's id."""
    clock = FrozenClock(T0)
    with world.engine.connect() as connection:
        found = connection.exec_driver_sql(
            "SELECT id, status FROM campaigns WHERE key = 'law_private_ai_us'"
        ).first()
    if found is not None and found[1] == "active":
        return str(found[0])
    checked = check_seed(
        _shipped(main.SEGMENTS_FILE_NAME, validate_segments),
        _shipped(main.MARKETS_FILE_NAME, validate_markets),
        _shipped(main.SEQUENCES_FILE_NAME, validate_sequences),
        _shipped(main.OFFERS_FILE_NAME, validate_offers),
        _shipped(main.CAMPAIGNS_FILE_NAME, validate_campaigns),
    )
    world.transact(lambda: Setup(clock).seed_sync(checked, clock.now()))
    with world.units.open():
        Setup(clock).save({"sender_name": "Test Sender"})
    with world.units.open():
        Sending(clock, env="prod").register_inbox("sender@example-sender.com", "Main")
    with world.engine.connect() as connection:
        campaign_id = str(
            connection.exec_driver_sql(
                "SELECT id FROM campaigns WHERE key = 'law_private_ai_us'"
            ).scalar_one()
        )
    with world.units.open():
        Campaigns(clock).activate(campaign_id)
    return campaign_id


def _enrollable_contact(world: World, tag: str) -> tuple[str, str]:
    """An eligible US Contact and the active US Campaign; their ids."""
    campaign_id = _enrollable(world)
    domain = f"firm-{tag}.com"
    identity = LeadIdentity(f"Firm {tag}", world.segment, web_domain=domain, country_code="US")
    upserted = world.transact(lambda: world.discovery.upsert_lead(identity, LeadSource.CSV))
    assert upserted.lead_id is not None
    contact_id = world.adder(
        upserted.lead_id,
        ContactCandidate(name=NAME, email=f"jane@{domain}"),
        ProvenanceSource.WEBSITE,
    )().contact_id
    assert contact_id is not None
    return contact_id, campaign_id


def _enroll(world: World, tag: str) -> tuple[Callable[[], object], Expect]:
    """Story 4.8: an eligible US Contact enrolled in the active US Campaign. Its gate call, then
    the one ``evaluate_eligibility`` makes, both before the first write; then (Story 4.9)
    ``request_steps``' before its ``drafts`` insert."""
    contact_id, campaign_id = _enrollable_contact(world, tag)
    sequencing = Sequencing(FrozenClock(T0))

    def action() -> object:
        return world.transact(
            lambda: sequencing.enroll(contact_id, campaign_id, EnrollmentOrigin.SELECTION, (1,))
        )

    gate = GateCall(ContactSubject(contact_id), EMAIL, "cold")
    return action, lambda: (gate, gate, gate)


def _request_steps(world: World, tag: str) -> tuple[Callable[[], object], Expect]:
    """Story 4.9: Step 2 requested for an Enrollment made (unrecorded) with Step 1 only: one
    gate call before its one ``drafts`` insert."""
    contact_id, campaign_id = _enrollable_contact(world, tag)
    clock = FrozenClock(T0)
    enrolled = world.transact(
        lambda: Sequencing(clock).enroll(
            contact_id, campaign_id, EnrollmentOrigin.SELECTION, (1,)
        )
    )
    assert isinstance(enrolled, NewEnrollment)
    drafting = Drafting(clock)

    def action() -> object:
        return world.transact(
            lambda: drafting.request_steps(enrolled.enrollment_id, (2,), Urgency.SOON)
        )

    return action, lambda: (GateCall(ContactSubject(contact_id), EMAIL, "cold"),)


def _requested(world: World) -> None:
    """An allowed request added a Step 2 slot (the scenario isn't vacuous)."""
    with world.engine.connect() as connection:
        steps = connection.exec_driver_sql(
            "SELECT step_no FROM drafts ORDER BY step_no, id"
        ).all()
    assert [row[0] for row in steps].count(2) == 1


def _enrolled(world: World) -> None:
    """An allowed enroll created an Enrollment (the scenario isn't vacuous)."""
    with world.engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM enrollments").scalar_one() == 1


def _pickable(world: World, tag: str) -> str:
    """Story 4.10: an eligible US Lead whose Contact is its Primary, and the one inbox active
    and warmed up for 14 days and more (so today has capacity); the Lead's id."""
    contact_id, _ = _enrollable_contact(world, tag)
    with world.engine.begin() as connection:
        lead_id = str(
            connection.exec_driver_sql(
                "SELECT lead_id FROM contacts WHERE id = ?", (contact_id,)
            ).scalar_one()
        )
        connection.exec_driver_sql(
            "UPDATE sending_inboxes SET status = 'active', warmup_started_on = '2026-10-01', "
            "warmup_method = 'manual', warm_level = 30"
        )
    world.transact(lambda: Research(FrozenClock(T0)).make_primary(lead_id, contact_id))
    return lead_id


def _pick(world: World, tag: str) -> tuple[Callable[[], object], Expect]:
    """Story 4.10: the Operator picks one eligible Lead: ``enroll``'s gate calls (its own and
    ``evaluate_eligibility``'s before the first write, then ``request_steps``'), and the
    ``daily_capacity`` row after them."""
    lead_id = _pickable(world, tag)
    clock = FrozenClock(T0)
    selection = Selection(clock, Sequencing(clock), compliance_api.Compliance(clock))
    with world.engine.connect() as connection:
        contact_id = str(
            connection.exec_driver_sql(
                "SELECT id FROM contacts WHERE lead_id = ?", (lead_id,)
            ).scalar_one()
        )

    def action() -> object:  # as the dashboard runs it: the use case in a unit of work
        with world.units.open():
            return selection.pick([lead_id])

    gate = GateCall(ContactSubject(contact_id), EMAIL, "cold")
    return action, lambda: (gate, gate, gate)


def _picked(world: World) -> None:
    """An allowed pick enrolled its Lead and counted it; the blocked one enrolled nothing."""
    with world.engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM enrollments").scalar_one() == 1
        assert connection.exec_driver_sql("SELECT picked FROM daily_capacity").all() == [(1,)]


def _published_page(lead_id: str, domain: str, text: str) -> None:
    """Store one crawl run of the Lead with one team page holding ``text``."""
    run_id = crawl_repo.insert_run(
        lead_id=lead_id,
        site_domain=domain,
        final_domain=domain,
        started_at=T0,
        ended_at=T0,
        outcome=str(CrawlOutcome.OK),
        error_code=None,
        pages_fetched=1,
        pages_skipped_robots=0,
        renders=0,
        errors=0,
    )
    url = f"https://{domain}/team"
    crawl_repo.insert_page(
        lead_id=lead_id,
        run_id=run_id,
        url=url,
        final_url=url,
        kind=str(PageKind.TEAM),
        fetched_at=T0,
        http_status=200,
        content_hash="0" * 64,
        text=text,
        text_purge_after=T0 + timedelta(days=30),
        changed_at=T0,
        mailto=(),
    )


def _find(world: World, tag: str) -> tuple[Callable[[], object], Expect]:
    """Story 2.13's ``find_contact_email`` on a named Contact without an address whose Lead's
    crawled team page publishes ``jane.doe@<domain>``: the Contact's gate call, then the found
    address's. The finders are scripted fakes that a published address never reaches."""
    domain = f"firm-{tag}.co.uk"
    email = f"jane.doe@{domain}"
    lead_id = world.lead(domain)
    contact_id = world.contact(lead_id, ContactCandidate(name=NAME))
    world.transact(lambda: _published_page(lead_id, domain, f"{NAME}, Managing Partner: {email}"))
    clock = FrozenClock(T0)
    finding = EmailFinding(
        clock,
        InMemoryUsageMeter(clock),
        world.research,
        queue_on(clock),
        domain_search=ScriptedDomainSearch(),
        hunter=ScriptedFinder(),
        prospeo=ScriptedFinder(),
    )

    def action() -> object:  # as the job runner runs it: the use case in a unit of work
        with world.units.open():
            return finding.find_contact_email(lead_id, contact_id)

    expected = (
        _gate(ContactSubject(contact_id)),
        _gate(NewContactSubject(lead_id, email=email, name=NAME)),
    )
    return action, lambda: expected


def _email_search_blocked(world: World) -> None:
    """A blocked search records its ``email_search_blocked`` event and gives no address."""
    assert world.last_event() == (
        "contacts",
        None,
        "email_search_blocked",
        str(BLOCKED.reasons[0]),
        "system:research",
    )
    with world.engine.connect() as connection:
        addresses = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM contacts WHERE email LIKE 'jane.doe@%'"
        ).scalar_one()
    assert addresses == 1  # the allowed case's only


def _seed_campaigns(world: World) -> str:
    """Every shipped seed file stored (once per world); the first Campaign's id."""

    def read(name: str) -> object:
        return seed_yaml.parse((main.SEED_DIR / name).read_text(encoding="utf-8"))

    with world.engine.connect() as connection:
        found = connection.exec_driver_sql("SELECT id FROM campaigns ORDER BY key").first()
    if found is None:
        with world.units.open():
            Seeds(FrozenClock(T0)).sync_seed(
                validate_segments(read(main.SEGMENTS_FILE_NAME)),
                validate_markets(read(main.MARKETS_FILE_NAME)),
                validate_sequences(read(main.SEQUENCES_FILE_NAME)),
                validate_company_facts(read(main.COMPANY_FACTS_FILE_NAME)),
                validate_offers(read(main.OFFERS_FILE_NAME)),
                validate_campaigns(read(main.CAMPAIGNS_FILE_NAME)),
            )
        with world.engine.connect() as connection:
            found = connection.exec_driver_sql("SELECT id FROM campaigns ORDER BY key").first()
    assert found is not None
    return str(found[0])


def _places(world: World, tag: str) -> tuple[Callable[[], object], Expect]:
    """Story 8.3's Places ingest (``PlaceDiscovery.ingest_candidate``) of one candidate whose
    own website is ``firm-<tag>.com``, inside a stored run: one gate call, through
    ``upsert_lead(places)``."""
    campaign = _seed_campaigns(world)
    location = DiscoveryLocation("Columbus, OH", "US")
    run_id = world.transact(
        lambda: runs_repo.insert(
            source="places",
            campaign_id=campaign,
            segment_id=world.segment,
            country_code="US",
            location=location.location,
            query_hash="0" * 16,
            status=RunStatus.RUNNING,
            started_at=T0,
            finished_at=None,
        )
    )
    domain = f"firm-{tag}.com"
    origin = f"https://www.{domain}"
    crawler = ScriptedCrawler(
        robots={f"{origin}/robots.txt": robots_txt(f"{origin}/robots.txt", ALLOW_ALL)},
        pages={f"{origin}/": html_page(f"{origin}/", f"<title>Firm {tag}</title>")},
    )
    clock = FrozenClock(T0)
    ingest = PlaceDiscovery(
        clock,
        PlaceSearches(clock, InMemoryUsageMeter(clock), ScriptedPlaces()),
        crawler,
        world.discovery,
        queue_on(clock),
    )
    candidate = PlaceCandidate(f"ChIJ-{tag}", f"{origin}/?utm=maps", "Maps Name", 39.96, -82.99)

    @use_case
    def ingest_one() -> object:  # as the discover_places run calls it: inside a use case
        return ingest.ingest_candidate(run_id, candidate, world.segment, location)

    def action() -> object:
        with world.units.open():
            return ingest_one()

    return action, lambda: (_gate(NewLeadSubject(domain)),)


# Story 2.14: each verify case's fakes, so the blocked check can see they were never called
VERIFY_FAKES: list[tuple[FakeDns, ScriptedVerifier]] = []


def _verify(world: World, tag: str) -> tuple[Callable[[], object], Expect]:
    """Story 2.14's ``verify_contact_email`` on the Primary Contact's ``guessed`` CSV address,
    its domain with an MX record and Hunter answering ``valid``: two gate calls, the Contact's
    before its first write and again before the paid Hunter call."""
    domain = f"verify-{tag}.co.uk"
    lead_id = world.lead(domain)
    contact_id = world.contact(lead_id, ContactCandidate(name=NAME, email=f"jane.doe@{domain}"))
    world.transact(lambda: contacts_repo.set_primary(contact_id, "operator", T0))
    clock = FrozenClock(T0)
    dns = FakeDns(mx={domain: (MxRecord(10, f"mx.{domain}"),)})
    verifier = ScriptedVerifier(VerifierAnswer("valid"))
    VERIFY_FAKES.append((dns, verifier))
    verification = EmailVerification(
        clock,
        InMemoryUsageMeter(clock),
        queue_on(clock),
        dns=dns,
        email_syntax=FakeEmailSyntax(),
        verifier=verifier,
    )

    def action() -> object:  # as the job runner runs it: the use case in a unit of work
        with world.units.open():
            return verification.verify_contact_email(lead_id, contact_id)

    return action, lambda: (_gate(ContactSubject(contact_id)),) * 2


def _verify_blocked(world: World) -> None:
    """A blocked check makes no DNS or Hunter call, keeps the class and records
    ``suppressed``; the allowed case verified its address (the scenario isn't vacuous)."""
    dns, verifier = VERIFY_FAKES[-1]
    assert dns.queries == [] and verifier.calls == []
    with world.engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT address_class, email_check_result FROM contacts "
            "WHERE email LIKE 'jane.doe@verify-%' ORDER BY email"
        ).all()
    assert [tuple(row) for row in rows] == [("verified", "valid"), ("guessed", "suppressed")]


def _channel_permissions() -> ChannelPermissions:
    clock = FrozenClock(T0)
    return ChannelPermissions(clock, Research(clock), Compliance(clock))


def _permission_contact(world: World, tag: str) -> str:
    """A Contact on a new Lead, with ``sender_timezone`` set (permission times are local)."""
    with world.units.open():
        Setup(FrozenClock(T0)).save({"sender_timezone": "Europe/London"})
    return world.contact(world.lead(f"firm-{tag}.co.uk"), ContactCandidate(name=NAME))


def _quietly(run: Callable[[], object]) -> object:
    """``run``, a refusal (the Blocked case) answered ``None``."""
    try:
        return run()
    except PermissionRefused:
        return None


def _permission_record(world: World, tag: str) -> tuple[Callable[[], object], Expect]:
    """Story 10.7: recording an Instagram permission gates the Contact on its channel."""
    contact_id = _permission_contact(world, tag)
    service = _channel_permissions()

    def action() -> object:
        with world.units.open():
            return _quietly(
                lambda: service.record(
                    contact_id,
                    "instagram",
                    "started_instagram_conversation",
                    "2026-11-18T08:00",
                    "They messaged us first on Instagram about the webinar",
                    "",
                    "jane.doe",
                )
            )

    expected = (GateCall(ContactSubject(contact_id), "instagram", "follow_up"),)
    return action, lambda: expected


def _permission_inbound(world: World, tag: str) -> tuple[Callable[[], object], Expect]:
    """Story 10.7: moving an Instagram permission's last inbound message gates it again."""
    contact_id = _permission_contact(world, tag)
    service = _channel_permissions()
    with world.units.open():
        permission_id = service.record(
            contact_id,
            "instagram",
            "started_instagram_conversation",
            "2026-11-18T07:00",
            "They messaged us first on Instagram",
            "",
            "jane.doe",
        )

    def action() -> object:
        with world.units.open():
            return _quietly(
                lambda: service.update_inbound(contact_id, permission_id, "2026-11-18T08:30")
            )

    expected = (GateCall(ContactSubject(contact_id), "instagram", "follow_up"),)
    return action, lambda: expected


SCENARIOS: Final[Mapping[str, Scenario]] = MappingProxyType(
    {
        "verify_contact_email": Scenario(
            (_verify,),
            # what a blocked check still writes: the Contact's result, never its class
            blocked_writes=frozenset({("UPDATE", "contacts")}),
            after_blocked=_verify_blocked,
        ),
        "find_contact_email": Scenario(
            (_find,),
            # what a blocked search still writes: its event and the Contact's search outcome
            blocked_rows={"events": 1},
            blocked_writes=frozenset({("INSERT", "events"), ("UPDATE", "contacts")}),
            after_blocked=_email_search_blocked,
        ),
        "discovery.upsert_lead": Scenario((_upsert,)),
        # Story 4.8: Blocked returns the refusal before any write, so nothing at all is added;
        # Story 4.9: request_steps gates again, after enroll's writes, before its drafts insert
        "sequencing.enroll": Scenario(
            (_enroll,), gates_first=False, gates_before_first_write=2, after_blocked=_enrolled
        ),
        # Story 4.9: Blocked answers before any write, so nothing at all is added
        "drafting.request_*": Scenario((_request_steps,), after_blocked=_requested),
        "discovery.places_ingest": Scenario(
            (_places,),
            # blocked, the run still counts the candidate as suppressed
            blocked_writes=frozenset({("UPDATE", "discovery_runs")}),
        ),
        # Story 10.7: Blocked stores nothing, not even the Evidence
        "pipeline.channel_permissions": Scenario((_permission_record, _permission_inbound)),
        # Story 4.10: enroll gates as above; a Blocked pick still records the day's capacity
        "scoring.pick": Scenario(
            (_pick,),
            gates_first=False,
            gates_before_first_write=2,
            blocked_rows={"daily_capacity": 1},
            blocked_writes=frozenset({("INSERT", "daily_capacity"), ("UPDATE", "daily_capacity")}),
            after_blocked=_picked,
        ),
        "discovery.csv_import": Scenario((_csv,), gates_first=False),
        "research.people_extraction": Scenario(
            (_people,),
            gates_first=False,  # one gate call per decision-maker, each before its own writes
            # what a run still records: the contact_blocked event, the lawyer count, the run
            # and the selection job (with its queued event); never a Contact, a Provenance
            # Record or person Evidence
            blocked_rows={"events": 2, "evidence": 1, "extraction_runs": 1, "jobs": 1},
            blocked_writes=frozenset(
                {
                    ("INSERT", "events"),
                    ("INSERT", "evidence"),
                    ("INSERT", "extraction_runs"),
                    ("INSERT", "jobs"),
                }
            ),
            after_blocked=_no_person_evidence_without_a_contact,
        ),
        **{
            f"research.add_contact[{source}]": Scenario(
                (_add_new(source), _add_match(source)),
                blocked_rows={"events": 1},
                blocked_writes=frozenset({("INSERT", "events")}),
                after_blocked=_contact_blocked,
            )
            for source in CONTACT_SOURCES
        },
    }
)


def run_case(
    world: World,
    scenario: Scenario,
    prepare: Prepare,
    monkeypatch: pytest.MonkeyPatch,
    answer: Allowed | Blocked,
    tag: str,
) -> tuple[Recorder, list[str]]:
    """Run one case once with the fake answering ``answer``; its recorder and problems."""
    action, expect = prepare(world, tag)
    before = table_counts(world.engine)
    with recording(world.engine, monkeypatch, answer) as recorder:
        action()
    after = table_counts(world.engine)
    if isinstance(answer, Allowed):
        expected = list(expect())
        problems = [] if recorder.calls == expected else [f"the gate saw {recorder.calls}"]
        problems += order_problems(recorder.log)
        if scenario.gates_first:
            problems += gates_first_problems(recorder.log)
        problems += leading_gates_problems(recorder.log, scenario.gates_before_first_write)
        return recorder, problems
    problems = gained_rows(before, after, scenario.blocked_rows)
    problems += blocked_write_problems(recorder.log, scenario.blocked_writes)
    if not recorder.calls:
        problems.append("the gate was never called")
    return recorder, problems


def test_the_manifest_names_are_distinct_and_the_scenarios_name_manifest_entries() -> None:
    assert len(NAMES) == len(set(NAMES))
    assert set(SCENARIOS) <= set(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_each_ad16_caller_calls_the_gate_before_writing(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not built(package_index())[name]:
        pytest.skip(f"not built yet: {name}")
    scenario = SCENARIOS.get(name)
    if scenario is None:
        pytest.fail(f"{name} is built but has no scenario")
    with world_at(tmp_path / "db") as world:
        for number, prepare in enumerate(scenario.cases):
            allowed, problems = run_case(
                world, scenario, prepare, monkeypatch, Allowed(), f"{number}-allowed"
            )
            assert problems == []
            assert any(isinstance(entry, Write) for entry in allowed.log)  # not vacuous
            _, problems = run_case(
                world, scenario, prepare, monkeypatch, BLOCKED, f"{number}-blocked"
            )
            assert problems == []
            if scenario.after_blocked is not None:
                scenario.after_blocked(world)


def test_the_built_callers_are_the_epic_2_paths() -> None:
    found = built(package_index())

    assert {name for name, defs in found.items() if defs} == {
        "discovery.upsert_lead",
        "discovery.csv_import",
        "discovery.places_ingest",
        *(f"research.add_contact[{source}]" for source in CONTACT_SOURCES),
        "research.people_extraction",
        "sequencing.enroll",  # Story 4.8
        "find_contact_email",
        "drafting.request_*",  # Story 4.9
        "verify_contact_email",  # Story 2.14
        "pipeline.channel_permissions",  # Story 10.7
        "scoring.pick",  # Story 4.10
    }
    finding = {d.qualname for d in found["find_contact_email"]}
    assert {"EmailFinding.find_contact_email", "EmailFinding._write"} <= finding
    assert all(name.startswith("EmailFinding.") for name in finding)
    verifying = {d.qualname for d in found["verify_contact_email"]}
    assert verifying == {"EmailVerification.verify_contact_email"}
    assert scenario_problems(package_index(), SCENARIOS) == []


# Probes: tmp_path trees -------------------------------------------------------------------------


def probe_tree(tmp_path: Path, files: Mapping[str, str]) -> Index:
    root = tmp_path / "nextron"
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return build_index(root)


MODEL_FILES: Final = {
    "db/models/leads.py": (
        "class LeadRecord(Base):\n    __tablename__ = 'leads'\n\n\n"
        "class NoteRecord(Base):\n    __tablename__ = 'notes'\n"
    ),
    "db/models/research.py": "class ContactRecord(Base):\n    __tablename__: str = 'contacts'\n",
}
LEADS_REPO: Final = (
    "from sqlalchemy import insert as insert_rows\n"
    "from sqlalchemy import select\n\n"
    "from nextron.db.models.leads import LeadRecord, NoteRecord\n\n\n"
    "def get(lead_id):\n    return select(LeadRecord)\n\n\n"
    "def insert(name):\n    return insert_rows(LeadRecord).values(name=name)\n\n\n"
    "def note(text):\n    return insert_rows(NoteRecord).values(text=text)\n"
)


def test_a_built_caller_without_a_scenario_fails(tmp_path: Path) -> None:
    index = probe_tree(
        tmp_path,
        {
            "app/sequencing/api.py": (
                "class Sequencing:\n    def enroll(self, contact_id):\n        pass\n\n"
                "    def enroll_contact(self, contact_id):\n        pass\n"
            ),
            "app/approval/api.py": (
                "class Approval:\n    def approve_draft(self, draft_id):\n        pass\n"
            ),
            "app/research/api.py": (
                "class Research:\n    def find_email_for_contact(self, contact_id):\n"
                "        pass\n\n    def ingest_signal_item(self, item):\n        pass\n"
            ),
            "app/research/finding.py": (
                "class EmailFinding:\n    def _write(self, contact_id):\n        pass\n"
            ),
            "app/replies/api.py": (
                "class Replies:\n    def intake(self, item):\n"
                "        self._research.add_contact(item)\n\n"
                "    def classify(self, item):\n        pass\n"
            ),
            "app/pipeline/api.py": (
                "class Pipeline:\n    def cancel_task(self, task_id):\n        pass\n\n"
                "    def create_call_sheet(self, lead_id):\n        pass\n"
            ),
        },
    )

    # Story 4.8's real scenario left out, so the probe's enroll has none
    without_enroll = [name for name in SCENARIOS if name != "sequencing.enroll"]
    assert scenario_problems(index, without_enroll) == [
        "signal_inbox is built (app/research/api.py Research.ingest_signal_item) but has no "
        "scenario",
        "replies.add_contact is built (app/replies/api.py Replies.intake) but has no scenario",
        "sequencing.enroll is built (app/sequencing/api.py Sequencing.enroll) but has no "
        "scenario",
        "approval.approve is built (app/approval/api.py Approval.approve_draft) but has no "
        "scenario",
        "pipeline.tasks_and_call_sheets is built (app/pipeline/api.py "
        "Pipeline.create_call_sheet) but has no scenario",
    ]
    found = built(index)
    assert [d.qualname for d in found["sequencing.enroll"]] == [
        "Sequencing.enroll",
        "Sequencing.enroll_contact",
    ]
    assert [d.qualname for d in found["signal_inbox"]] == ["Research.ingest_signal_item"]
    assert [d.qualname for d in found["find_contact_email"]] == [
        "Research.find_email_for_contact",
        "EmailFinding._write",
    ]
    assert scenario_problems(index, [n for n in NAMES if n != "signal_inbox"]) == [
        "signal_inbox is built (app/research/api.py Research.ingest_signal_item) but has no "
        "scenario"
    ]
    assert scenario_problems(index, [*SCENARIOS, *NAMES]) == []


def test_the_call_site_check_names_a_stray_caller(tmp_path: Path) -> None:
    index = probe_tree(
        tmp_path,
        {
            "app/ops/api.py": (
                "class Ops:\n    def sweep(self, identity, source):\n"
                "        self._discovery.upsert_lead(identity, source)\n"
            ),
            "web/probe.py": "def page(research):\n    add = research.add_contact\n    add()\n",
            "jobs/probe.py": "from x import add_contact\n\nadd_contact()\n",
            "app/discovery/api.py": (
                "class LeadImports:\n    def run(self, row):\n"
                "        def each():\n            self._research.add_contact(row)\n\n"
                "        self._discovery.upsert_lead(row)\n        each()\n"
            ),
        },
    )

    assert call_site_problems(index) == [
        "app/ops/api.py:3 Ops.sweep uses upsert_lead outside the AD-16 manifest",
        "jobs/probe.py:3 <module> uses add_contact outside the AD-16 manifest",
        "web/probe.py:2 page uses add_contact outside the AD-16 manifest",
    ]


def test_the_insert_scan_finds_the_guarded_repository_inserts() -> None:
    found = inserting_functions(package_index())

    assert found[("db/repos/discovery/leads.py", "insert")] == {"leads"}
    assert found[("db/repos/research/contacts.py", "insert")] == {"contacts"}
    assert found[("db/repos/sequencing/enrollments.py", "insert")] == {"enrollments"}  # 4.8
    assert found[("db/repos/drafting/drafts.py", "insert_slots")] == {"drafts"}  # 4.9
    assert set(found.values()) <= {
        frozenset({"leads"}),
        frozenset({"contacts"}),
        frozenset({"enrollments"}),
        frozenset({"drafts"}),
    }
    assert guarded_models(package_index()) == {
        "LeadRecord": "leads",
        "ContactRecord": "contacts",
        "EnrollmentRecord": "enrollments",
        "DraftRecord": "drafts",
    }


@pytest.mark.parametrize(
    ("files", "found"),
    [
        (  # a stray insert
            {
                "app/ops/api.py": (
                    "from nextron.db.repos.discovery import leads as leads_repo\n\n\n"
                    "def seed(name):\n    leads_repo.insert(name)\n"
                ),
            },
            [
                "app/ops/api.py:5 seed uses db/repos/discovery/leads.py insert, which inserts "
                "into leads, outside an AD-16 manifest def listing it"
            ],
        ),
        (  # a manifest def that doesn't list the table
            {
                "app/discovery/api.py": (
                    "import nextron.db.repos.discovery.leads as repo\n\n\n"
                    "class LeadImports:\n    def run(self, name):\n        repo.insert(name)\n"
                ),
            },
            [
                "app/discovery/api.py:6 LeadImports.run uses db/repos/discovery/leads.py "
                "insert, which inserts into leads, outside an AD-16 manifest def listing it"
            ],
        ),
        (  # imported by name, through the parent package, and a reference
            {
                "app/ops/api.py": (
                    "from nextron.db.repos.discovery.leads import insert as add\n"
                    "from nextron.db.repos import discovery\n\n\n"
                    "def seed(name):\n    add(name)\n    make = discovery.leads.insert\n"
                ),
            },
            [
                "app/ops/api.py:6 seed uses db/repos/discovery/leads.py insert, which inserts "
                "into leads, outside an AD-16 manifest def listing it",
                "app/ops/api.py:7 seed uses db/repos/discovery/leads.py insert, which inserts "
                "into leads, outside an AD-16 manifest def listing it",
            ],
        ),
    ],
)
def test_the_insert_scan_names_a_stray_insert(
    files: dict[str, str], found: list[str], tmp_path: Path
) -> None:
    repo = {"db/repos/discovery/leads.py": LEADS_REPO}
    index = probe_tree(tmp_path, {**MODEL_FILES, **repo, **files})

    assert insert_problems(index) == found


def test_the_insert_scan_allows_the_owner_the_manifest_and_unguarded_tables(
    tmp_path: Path,
) -> None:
    index = probe_tree(
        tmp_path,
        {
            **MODEL_FILES,
            "db/repos/discovery/leads.py": LEADS_REPO + "\n\ndef again(name):\n    insert(name)\n",
            "app/discovery/api.py": (
                "from nextron.db.repos.discovery import leads as leads_repo\n\n\n"
                "class Discovery:\n    def upsert_lead(self, name):\n"
                "        leads_repo.get(name)\n        leads_repo.insert(name)\n"
                "        leads_repo.note(name)\n"
            ),
            "app/ops/api.py": (
                "from nextron.db.repos.discovery import leads as leads_repo\n\n\n"
                "def tidy(name):\n    leads_repo.get(name)\n    leads_repo.note(name)\n"
            ),
        },
    )

    assert set(inserting_functions(index)) == {
        ("db/repos/discovery/leads.py", "insert"),
        ("db/repos/discovery/leads.py", "again"),
    }
    assert insert_problems(index) == []


@pytest.mark.parametrize(
    "body",
    [
        "    current().session.add(ContactRecord(id=contact_id))\n",
        "    current().session.execute(ContactRecord.__table__.insert().values(id=contact_id))\n",
        "    current().session.execute(models.ContactRecord.__table__.insert())\n",
        "    current().session.execute(sa.insert(Contact).values(id=contact_id))\n",
        "    current().session.execute(sqlite_insert(models.ContactRecord))\n",
        "    current().session.execute(insert(ContactRecord.__table__))\n",
        "    current().session.execute(sa.insert(models.ContactRecord.__table__))\n",
        "    current().session.execute(insert(table('contacts', column('id'))))\n",
        "    current().session.execute(sa.insert(sa.table('contacts')))\n",
        "    current().session.execute(sa.table('contacts').insert())\n",
        "    current().session.bulk_insert_mappings(ContactRecord, [{'id': contact_id}])\n",
        "    current().session.bulk_save_objects(models.ContactRecord, [contact_id])\n",
    ],
)
def test_the_insert_scan_catches_a_constructed_model(body: str, tmp_path: Path) -> None:
    repo = (
        "import sqlalchemy as sa\n"
        "from sqlalchemy import column, insert, table\n"
        "from sqlalchemy.dialects.sqlite import insert as sqlite_insert\n\n"
        "from nextron.db import models\n"
        "from nextron.db.models.research import ContactRecord\n"
        "from nextron.db.models.research import ContactRecord as Contact\n\n\n"
        "def add(contact_id):\n" + body
    )
    index = probe_tree(
        tmp_path,
        {
            **MODEL_FILES,
            "db/repos/research/contacts.py": repo,
            "app/ops/api.py": (
                "from nextron.db.repos.research import contacts\n\n\n"
                "class Ops:\n    def seed(self, contact_id):\n        contacts.add(contact_id)\n"
            ),
        },
    )

    assert inserting_functions(index) == {("db/repos/research/contacts.py", "add"): {"contacts"}}
    assert insert_problems(index) == [
        "app/ops/api.py:6 Ops.seed uses db/repos/research/contacts.py add, which inserts into "
        "contacts, outside an AD-16 manifest def listing it"
    ]


def test_no_string_inserts_into_a_guarded_table() -> None:
    assert raw_sql_problems(package_index()) == []


@pytest.mark.parametrize(
    ("source", "found"),
    [
        (
            "SQL = 'INSERT INTO contacts (id) VALUES (?)'\n",
            "app/ops/api.py:1 holds INSERT INTO contacts",
        ),
        (
            "SQL = 'insert or ignore into \"leads\" (id) values (1)'\n",
            "app/ops/api.py:1 holds INSERT INTO leads",
        ),
        (
            "x = 1\nSQL = f'REPLACE INTO main.drafts VALUES ({x})'\n",
            "app/ops/api.py:2 holds INSERT INTO drafts",
        ),
        (
            "SQL = 'INSERT INTO \"main\".\"leads\" (id) VALUES (1)'\n",
            "app/ops/api.py:1 holds INSERT INTO leads",
        ),
        (
            "SQL = 'INSERT INTO [main].[contacts] (id) VALUES (1)'\n",
            "app/ops/api.py:1 holds INSERT INTO contacts",
        ),
        (
            "def f():\n    '''Docs.'''\n"
            "    return 'INSERT INTO outbound_messages DEFAULT VALUES'\n",
            "app/ops/api.py:3 holds INSERT INTO outbound_messages",
        ),
    ],
)
def test_the_raw_sql_check_catches_an_insert_string(
    source: str, found: str, tmp_path: Path
) -> None:
    index = probe_tree(tmp_path, {"app/ops/api.py": source})

    assert raw_sql_problems(index) == [found]


def test_the_raw_sql_check_allows_docstrings_migrations_and_other_tables(tmp_path: Path) -> None:
    index = probe_tree(
        tmp_path,
        {
            "app/ops/api.py": (
                '"""Never runs INSERT INTO leads."""\n\n'
                "SQL = 'INSERT INTO leads_archive (id) VALUES (1)'\n"
                "OTHER = 'INSERT INTO notes (id) VALUES (1)'\n"
            ),
            "db/alembic/versions/0099_x.py": "SQL = 'INSERT INTO leads (id) VALUES (1)'\n",
        },
    )

    assert raw_sql_problems(index) == []


CONTACTS_REPO_FILE: Final = (
    "def fill(contact_id, updated_at, **fields):\n    pass\n\n\n"
    "def set_address(contact_id, *, email, address_class, at):\n    pass\n\n\n"
    "def again(contact_id, at, **fields):\n"
    "    fill(contact_id, at, **fields)\n    set_address(contact_id, email='a', "
    "address_class='b', at=at)\n"
)
REPO_ALIAS: Final = "from nextron.db.repos.research import contacts as contacts_repo\n\n\n"


@pytest.mark.parametrize(
    ("source", "found"),
    [
        (  # a stray set_address call
            REPO_ALIAS
            + "def tidy(cid, at):\n    contacts_repo.set_address(cid, email='a', at=at)\n",
            "app/ops/api.py:5 tidy uses set_address",
        ),
        (  # set_address imported by name, as a reference
            "from nextron.db.repos.research.contacts import set_address as put\n\n\n"
            "def tidy():\n    make = put\n    return make\n",
            "app/ops/api.py:5 tidy uses set_address",
        ),
        (  # through the parent package, at module level
            "from nextron.db.repos import research\n\nresearch.contacts.set_address('a')\n",
            "app/ops/api.py:3 <module> uses set_address",
        ),
        (  # fill with an email keyword
            REPO_ALIAS + "def tidy(cid, at):\n    contacts_repo.fill(cid, at, email='a')\n",
            "app/ops/api.py:5 tidy calls fill with email or **",
        ),
        (  # fill with a ** splat, the module imported whole
            "import nextron.db.repos.research.contacts\n\n\n"
            "def tidy(cid, at, fields):\n"
            "    nextron.db.repos.research.contacts.fill(cid, at, **fields)\n",
            "app/ops/api.py:5 tidy calls fill with email or **",
        ),
        (  # fill imported by name, with a splat
            "from nextron.db.repos.research.contacts import fill\n\n\n"
            "def tidy(cid, at, fields):\n    fill(cid, at, **fields)\n",
            "app/ops/api.py:5 tidy calls fill with email or **",
        ),
        (  # fill aliased, so its keywords can't be read
            REPO_ALIAS + "def tidy(cid, at):\n    put = contacts_repo.fill\n    put(cid, at)\n",
            "app/ops/api.py:5 tidy references fill outside a call",
        ),
    ],
)
def test_the_update_scan_names_a_stray_email_write(
    source: str, found: str, tmp_path: Path
) -> None:
    index = probe_tree(
        tmp_path, {"db/repos/research/contacts.py": CONTACTS_REPO_FILE, "app/ops/api.py": source}
    )

    assert update_problems(index) == [
        f"{found} of db/repos/research/contacts.py outside the AD-16 manifest"
    ]


def test_the_update_scan_allows_the_owner_the_manifest_and_other_fills(tmp_path: Path) -> None:
    index = probe_tree(
        tmp_path,
        {
            "db/repos/research/contacts.py": CONTACTS_REPO_FILE,
            "app/research/api.py": REPO_ALIAS
            + (
                "class Research:\n    def add_contact(self, cid, at, fills):\n"
                "        contacts_repo.fill(cid, at, **fills)\n"
                "        contacts_repo.fill(cid, at, email='a')\n\n"
                "    def _resolve(self, cid, at, market):\n"
                "        contacts_repo.fill(cid, at, market_id=market, market_basis=None)\n"
            ),
            "app/research/finding.py": REPO_ALIAS
            + (
                "class EmailFinding:\n    def _write(self, cid, at):\n"
                "        contacts_repo.set_address(cid, email='a', address_class='b', at=at)\n"
            ),
            "app/ops/api.py": (  # another module's fill and set_address
                "from nextron.app.forms import fill\n\n\n"
                "def tidy(form, row, fields):\n    fill(form, email='a', **fields)\n"
                "    row.set_address('a')\n    row.fill(email='a')\n"
            ),
        },
    )

    assert update_problems(index) == []
    assert update_problems(index, manifest=()) == [  # the manifest is what allows them
        "app/research/api.py:6 Research.add_contact calls fill with email or ** of "
        "db/repos/research/contacts.py outside the AD-16 manifest",
        "app/research/api.py:7 Research.add_contact calls fill with email or ** of "
        "db/repos/research/contacts.py outside the AD-16 manifest",
        "app/research/finding.py:6 EmailFinding._write uses set_address of "
        "db/repos/research/contacts.py outside the AD-16 manifest",
    ]


def test_the_real_tree_writes_contact_addresses_only_through_the_manifest() -> None:
    index = package_index()
    uses = {
        (path, chain[-1] if chain else "<module>")
        for path, _, name, chain, _ in index.names
        if name == SET_ADDRESS
    }

    assert update_problems(index) == []
    assert ("app/research/finding.py", "EmailFinding._write") in uses  # not vacuous
    stray = update_problems(index, manifest=())
    assert any("Research.add_contact calls fill" in problem for problem in stray)
    assert any("EmailFinding._write uses set_address" in problem for problem in stray)
    assert not any("Research._resolve" in problem for problem in stray)


def test_the_real_tree_has_no_stray_caller_or_insert() -> None:
    index = package_index()
    uses = [
        (path, chain[-1] if chain else "<module>", name)
        for path, _, name, chain, _ in index.names
        if name in GATED_COMMANDS
    ]

    assert call_site_problems(index) == []
    assert insert_problems(index) == []
    assert ("app/discovery/api.py", "LeadImports.run", "upsert_lead") in uses  # not vacuous
    assert ("app/discovery/api.py", "LeadImports.run", "add_contact") in uses


# Probes: the scenario checks --------------------------------------------------------------------

WRITE_FIRST: Final = """
from nextron.app.compliance import api as compliance_api
from nextron.domain.contactability import Blocked, NewLeadSubject


def create(connection):
    connection.exec_driver_sql("INSERT INTO notes (id) VALUES ('a')")
    compliance_api.assert_contactable(NewLeadSubject("firm.co.uk"), "email", "discovery")
    connection.exec_driver_sql("INSERT INTO leads (id) VALUES ('a')")
"""
ONE_GATE_TWO_LEADS: Final = """
from nextron.app.compliance import api as compliance_api
from nextron.domain.contactability import NewLeadSubject


def create(connection):
    for domain in ("one.co.uk", "two.co.uk"):
        if domain == "one.co.uk":
            compliance_api.assert_contactable(NewLeadSubject(domain), "email", "discovery")
        connection.exec_driver_sql("INSERT INTO leads (id) VALUES (?)", (domain,))
"""
BLOCKED_IGNORED: Final = """
from nextron.app.compliance import api as compliance_api
from nextron.domain.contactability import NewLeadSubject


def create(connection):
    compliance_api.assert_contactable(NewLeadSubject("firm.co.uk"), "email", "discovery")
    connection.exec_driver_sql("INSERT INTO leads (id) VALUES ('a')")
"""
BLOCKED_THEN_UPDATE: Final = """
from nextron.app.compliance import api as compliance_api
from nextron.domain.contactability import Blocked, NewLeadSubject


def create(connection):
    answer = compliance_api.assert_contactable(NewLeadSubject("firm.co.uk"), "email", "discovery")
    if isinstance(answer, Blocked):
        connection.exec_driver_sql("UPDATE leads SET id = 'b'")
        connection.exec_driver_sql("DELETE FROM notes")
        return
"""
WRITE_BETWEEN_GATES: Final = """
from nextron.app.compliance import api as compliance_api
from nextron.domain.contactability import NewLeadSubject


def create(connection):
    compliance_api.assert_contactable(NewLeadSubject("firm.co.uk"), "email", "discovery")
    connection.exec_driver_sql("UPDATE notes SET id = 'b'")
    compliance_api.assert_contactable(NewLeadSubject("firm.co.uk"), "email", "discovery")
"""
GATED_PROPERLY: Final = """
from nextron.app.compliance import api as compliance_api
from nextron.domain.contactability import Blocked, NewLeadSubject


def create(connection):
    answer = compliance_api.assert_contactable(NewLeadSubject("firm.co.uk"), "email", "discovery")
    if isinstance(answer, Blocked):
        return
    connection.exec_driver_sql("INSERT INTO leads (id) VALUES ('a')")
    connection.exec_driver_sql("INSERT INTO notes (id) VALUES ('a')")
"""


def _probe_module(tmp_path: Path, source: str) -> ModuleType:
    path = tmp_path / "probe_ad16_caller.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("probe_ad16_caller", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def probe_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = sqlalchemy.create_engine(f"sqlite:///{(tmp_path / 'probe.db').as_posix()}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE leads (id TEXT)")
            connection.exec_driver_sql("CREATE TABLE notes (id TEXT)")
        yield engine
    finally:
        engine.dispose()


def _run_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, answer: Allowed | Blocked
) -> tuple[list[str], list[str]]:
    """The order problems and the gained rows of one probe module's ``create``."""
    log, gained, _ = _probe_log(tmp_path, monkeypatch, source, answer)
    return order_problems(log), gained


def _probe_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, answer: Allowed | Blocked
) -> tuple[list[GateCall | Write], list[str], list[str]]:
    """One probe module's ``create``: its log, its gained rows, and its writes after a
    ``Blocked`` answer."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    module = _probe_module(tmp_path, source)
    with probe_engine(tmp_path) as engine:
        before = table_counts(engine)
        with recording(engine, monkeypatch, answer) as recorder, engine.begin() as connection:
            module.create(connection)
        gained = gained_rows(before, table_counts(engine), {})
        return recorder.log, gained, blocked_write_problems(recorder.log, frozenset())


def test_the_order_check_catches_a_write_before_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order, _ = _run_probe(tmp_path, monkeypatch, WRITE_FIRST, Allowed())

    assert order == ["INSERT notes comes before the gate call"]


def test_the_order_check_catches_a_guarded_insert_without_its_own_gate_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order, _ = _run_probe(tmp_path, monkeypatch, ONE_GATE_TWO_LEADS, Allowed())

    assert order == ["INSERT leads has no gate call of its own"]


def test_the_blocked_check_catches_an_ignored_blocked_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order, gained = _run_probe(tmp_path, monkeypatch, BLOCKED_IGNORED, BLOCKED)

    assert order == []
    assert gained == ["leads gained 1 row(s)"]


def test_the_blocked_check_catches_an_update_or_delete_after_a_blocked_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, gained, writes = _probe_log(tmp_path, monkeypatch, BLOCKED_THEN_UPDATE, BLOCKED)

    assert gained == []  # no row was added, so only the write log catches it
    assert writes == ["UPDATE leads after a Blocked answer", "DELETE notes after a Blocked answer"]
    events = frozenset({("INSERT", "events")})
    assert blocked_write_problems([Write("INSERT", "events")], events) == []


def test_the_gates_first_check_catches_a_write_between_gate_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log, _, _ = _probe_log(tmp_path, monkeypatch, WRITE_BETWEEN_GATES, Allowed())

    assert order_problems(log) == []
    assert gates_first_problems(log) == ["a gate call comes after UPDATE notes"]


def test_the_leading_gates_check_names_a_second_gate_call_after_a_write() -> None:
    gate = GateCall(None, EMAIL, "cold")
    late = [gate, Write("INSERT", "enrollments"), gate, Write("INSERT", "drafts")]
    early = [gate, gate, Write("INSERT", "enrollments"), gate, Write("INSERT", "drafts")]

    assert leading_gates_problems(late, 2) == [
        "only 1 of 2 gate calls come before the first write"
    ]
    assert leading_gates_problems(early, 2) == []
    assert leading_gates_problems(late, 0) == []


def test_the_scenario_checks_pass_a_caller_that_gates_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order, gained = _run_probe(tmp_path / "allowed", monkeypatch, GATED_PROPERLY, Allowed())
    assert order == []
    assert gained == ["leads gained 1 row(s)", "notes gained 1 row(s)"]  # allowed: it writes

    order, gained = _run_probe(tmp_path / "blocked", monkeypatch, GATED_PROPERLY, BLOCKED)
    assert order == []
    assert gained == []


def test_the_recorder_is_removed_after_the_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = compliance_api.assert_contactable
    with probe_engine(tmp_path) as engine:
        with recording(engine, monkeypatch, Allowed()) as recorder:
            assert compliance_api.assert_contactable == recorder.gate
        assert compliance_api.assert_contactable is real
        with engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO leads (id) VALUES ('a')")
        assert recorder.log == []
