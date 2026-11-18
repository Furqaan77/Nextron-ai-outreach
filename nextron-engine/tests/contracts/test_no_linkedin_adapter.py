"""FR-49 by construction (Story 10.1): the engine has no LinkedIn or Instagram adapter, port, job
type or code that names the platforms' hosts, and nothing can add one without failing here.

- **Module names.** No module or package under ``nextron/`` has ``linkedin`` or ``instagram``
  (any case) in any path segment, except those in ``PLATFORM_MODULE_ALLOWLIST`` (empty: a
  future pure helper would go there, in ``nextron.domain`` only).
- **Defined names.** A name holding either word, defined at module level (also under a
  module-level ``if``, ``try``, ``with``, ``for`` or ``while``), as a method or nested class of a
  class, or as an import's ``as`` alias, is one of ``PLATFORM_NAME_ALLOWLIST`` (each a
  ``"<module>.<qualified name>"`` in ``nextron.domain``, with its reason). Function bodies and
  class attribute fields aren't read.
- **Ports.** Every ``Protocol`` class in ``nextron.ports`` is one of AD-9's ports or one of
  the approved extras, each with its reason.
- **Job types.** No job type ``build_job_types`` registers has either word in its name, except
  those in ``PLATFORM_JOB_TYPE_ALLOWLIST`` (empty; Stories 10.4 and 10.5 add theirs with a
  reason: they prepare and deliver Tasks a person sends by hand, never fetch or post).
- **Host literals.** The platforms' hosts (``linkedin.com``, ``lnkd.in``, ``licdn.com``,
  ``instagram.com``, ``instagr.am``, ``cdninstagram.com``; ``lnkd.info`` or
  ``linkedin.company.example`` don't count) appear in a Python string that isn't a docstring
  only as one of ``HOST_LITERAL_ALLOWLIST``'s exact ``(module, literal)`` pairs, and in no
  template, stylesheet, script or
  other file under ``nextron/`` (the vendored htmx excluded), so no page embeds a LinkedIn badge
  or an Instagram widget and no code builds a platform URL.

Every allowlist entry must exist, and each check has a negative probe on a synthetic tree.
"""

import ast
import functools
import inspect
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest

from nextron import main
from tests.contracts.support import PACKAGE

PLATFORM_WORD: Final = re.compile(r"linkedin|instagram", re.IGNORECASE)
PLATFORM_HOST: Final = re.compile(
    r"(?<![a-z0-9-])(?:linkedin\.com|lnkd\.in|licdn\.com|instagram\.com|instagr\.am"
    r"|cdninstagram\.com)(?![a-z0-9-]|\.[a-z0-9])",
    re.IGNORECASE,
)
VENDORED: Final = re.compile(r"^htmx-[0-9.]+\.min\.js$")

# module -> reason; only nextron.domain.* (pure helpers that fetch nothing)
PLATFORM_MODULE_ALLOWLIST: Final[dict[str, str]] = {}
# "<module>.<qualified name>" -> reason; only in nextron.domain.*
PLATFORM_NAME_ALLOWLIST: Final[dict[str, str]] = {
    "nextron.domain.identity.normalize_linkedin_url": "the one profile URL normaliser (FR-49)",
    "nextron.domain.identity.LINKEDIN_HOST": "the host the normaliser accepts",
    "nextron.domain.identity.LINKEDIN_PROFILE_PREFIX": "the normalised profile URL's prefix",
    "nextron.domain.identity.LINKEDIN_PROFILE_SEGMENT": "the profile path segment, in",
    "nextron.domain.lead_import.NOTE_BAD_LINKEDIN": "the CSV import's note on a bad URL",
    "nextron.domain.lead_import._Planner._linkedin": "reads a CSV cell's LinkedIn URL",
    "nextron.domain.contacts.LINKEDIN_URL_SOURCES": "where a LinkedIn URL may come from",
}
PLATFORM_JOB_TYPE_ALLOWLIST: Final[dict[str, str]] = {}
# (module, exact string literal) -> reason; only in nextron.domain.*
HOST_LITERAL_ALLOWLIST: Final[dict[tuple[str, str], str]] = {
    **{
        ("nextron.domain.fetch_policy", host): "an entry of the FR-49 denylist"
        for host in (
            "linkedin.com",
            "lnkd.in",
            "instagram.com",
            "instagr.am",
            "licdn.com",
            "cdninstagram.com",
        )
    },
    ("nextron.domain.identity", "linkedin.com"): "LINKEDIN_HOST, the host a profile URL is on",
    ("nextron.domain.identity", "https://www.linkedin.com/in/"): "the normalised URL's prefix",
    ("nextron.domain.identity", "is not on linkedin.com"): "the normaliser's refusal message",
    (
        "nextron.domain.lead_import",
        "the LinkedIn URL isn't a linkedin.com/in/ profile, so it was not imported",
    ): "NOTE_BAD_LINKEDIN, the CSV import's note",
}
AD9_PORTS: Final = frozenset(
    {
        "MailPort",
        "MailboxPort",
        "AlertPort",
        "UnsubscribePort",
        "PlacesPort",
        "RegistryPort",
        "EmailFinderPort",
        "JobBoardPort",
        "CrawlerPort",
        "RendererPort",
        "DnsPort",
        "PostmasterPort",
        "LlmPort",
        "BackupTargetPort",
        "TokenCodecPort",
        "SecretsPort",
        "UsageMeterPort",
        "Clock",
    }
)
APPROVED_EXTRA_PORTS: Final[dict[str, str]] = {
    "EmailSyntaxPort": "Story 1.9: email_validator's syntax check behind a port",
    "GoogleOAuthPort": "Story 2.3: the Google OAuth consent flow",
    "ConsentListener": "Story 2.3: the loopback listener the consent flow waits on",
    "ClientFilePort": "Story 2.3: reading the Google client secrets file",
}


# The checks: each takes the root holding ``nextron/`` and returns its problems ----------------


@functools.cache
def parse(path: Path) -> ast.Module:
    """The module at ``path``, parsed once per test run (every path read here is fixed or a
    fresh ``tmp_path``)."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _modules(root: Path) -> Iterator[tuple[str, Path]]:
    """Every Python module under ``root/nextron`` as ``(dotted name, path)``."""
    for path in sorted((root / "nextron").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(root).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        yield ".".join(parts), path


def module_name_problems(root: Path, allowlist: dict[str, str]) -> list[str]:
    problems = []
    for name, _ in _modules(root):
        if PLATFORM_WORD.search(name) and name not in allowlist:
            problems.append(f"{name}: a module named for LinkedIn or Instagram")
    return problems


COMPOUND: Final = (ast.If, ast.Try, ast.TryStar, ast.With, ast.For, ast.While)


def _defined_names(body: list[ast.stmt], prefix: str = "") -> Iterator[str]:
    """The qualified names ``body`` defines: see the module docstring."""
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield prefix + node.name
        elif isinstance(node, ast.ClassDef):
            yield prefix + node.name
            members = [
                inner
                for inner in node.body
                if isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            ]
            yield from _defined_names(members, f"{prefix}{node.name}.")
        elif isinstance(node, ast.TypeAlias):
            yield prefix + node.name.id
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for found in ast.walk(target):
                    if isinstance(found, ast.Name):
                        yield prefix + found.id
        elif isinstance(node, ast.Import | ast.ImportFrom):
            yield from (prefix + alias.asname for alias in node.names if alias.asname)
        elif isinstance(node, COMPOUND):
            for field in ("body", "orelse", "finalbody"):
                yield from _defined_names(getattr(node, field, []), prefix)
            for handler in getattr(node, "handlers", []):
                yield from _defined_names(handler.body, prefix)


def top_level_name_problems(root: Path, allowlist: dict[str, str]) -> list[str]:
    problems = []
    for name, path in _modules(root):
        problems.extend(
            f"{name}.{defined}: named for LinkedIn or Instagram outside the allowlist"
            for defined in _defined_names(parse(path).body)
            if PLATFORM_WORD.search(defined) and f"{name}.{defined}" not in allowlist
        )
    return problems


def _is_protocol_base(base: ast.expr) -> bool:
    if isinstance(base, ast.Subscript):
        base = base.value
    return (isinstance(base, ast.Name) and base.id == "Protocol") or (
        isinstance(base, ast.Attribute) and base.attr == "Protocol"
    )


def port_problems(root: Path, allowed: frozenset[str]) -> list[str]:
    problems = []
    for name, path in _modules(root):
        if not (name == "nextron.ports" or name.startswith("nextron.ports.")):
            continue
        tree = parse(path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ClassDef)
                and any(_is_protocol_base(base) for base in node.bases)
                and node.name not in allowed
            ):
                problems.append(f"{name}.{node.name}: a port outside AD-9 and the approved extras")
    return problems


def job_type_problems(names: tuple[str, ...], allowlist: dict[str, str]) -> list[str]:
    return [
        f"{name}: a job type named for LinkedIn or Instagram"
        for name in names
        if PLATFORM_WORD.search(name) and name not in allowlist
    ]


def _docstrings(tree: ast.Module) -> set[int]:
    """The ids of every docstring constant in ``tree``."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def _host_literals(path: Path) -> list[tuple[int, str]]:
    """``(line, text)`` of every non-docstring string in the module at ``path`` naming a
    platform host."""
    tree = parse(path)
    docstrings = _docstrings(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and PLATFORM_HOST.search(node.value)
    ]


def host_literal_problems(root: Path, allowlist: dict[tuple[str, str], str]) -> list[str]:
    problems = []
    for name, path in _modules(root):
        problems.extend(
            f"{name} (l.{line}): a LinkedIn or Instagram host"
            for line, text in _host_literals(path)
            if (name, text) not in allowlist
        )
    for path in sorted((root / "nextron").rglob("*")):
        if (
            not path.is_file()
            or path.suffix in {".py", ".pyc"}
            or "__pycache__" in path.parts
            or VENDORED.match(path.name)
        ):
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        if PLATFORM_HOST.search(text):
            label = path.relative_to(root).as_posix()
            problems.append(f"{label}: a LinkedIn or Instagram host in a non-Python file")
    return problems


def _job_type_names() -> tuple[str, ...]:
    """The names ``build_job_types`` registers, built with a stub for every service, defaulted
    ones included, so a type registered only when its service is given is read too (none
    runs)."""

    def no_service() -> Any:
        raise AssertionError("no job runs here")

    parameters = inspect.signature(main.build_job_types).parameters
    services = {name: no_service for name in parameters}
    return main.build_job_types(**services).names()


ROOT: Final = PACKAGE.parent


# The tree as committed ------------------------------------------------------------------------


def test_no_linkedin_adapter() -> None:
    assert module_name_problems(ROOT, PLATFORM_MODULE_ALLOWLIST) == []
    assert top_level_name_problems(ROOT, PLATFORM_NAME_ALLOWLIST) == []
    assert port_problems(ROOT, AD9_PORTS | APPROVED_EXTRA_PORTS.keys()) == []
    assert job_type_problems(_job_type_names(), PLATFORM_JOB_TYPE_ALLOWLIST) == []
    assert host_literal_problems(ROOT, HOST_LITERAL_ALLOWLIST) == []


def test_every_job_type_is_read() -> None:
    names = _job_type_names()
    assert "enrich_lead" in names
    assert len(names) == len(set(names))


@pytest.mark.parametrize(
    "allowlist",
    [
        PLATFORM_MODULE_ALLOWLIST,
        PLATFORM_NAME_ALLOWLIST,
        PLATFORM_JOB_TYPE_ALLOWLIST,
        HOST_LITERAL_ALLOWLIST,
        APPROVED_EXTRA_PORTS,
    ],
)
def test_every_allowlist_entry_has_a_reason(allowlist: dict[str, str]) -> None:
    for entry, reason in allowlist.items():
        assert reason.strip() and "\n" not in reason, entry


def test_the_module_allowlist_holds_only_existing_domain_modules() -> None:
    modules = dict(_modules(ROOT))
    for module in PLATFORM_MODULE_ALLOWLIST:
        assert module.startswith("nextron.domain."), module
        assert module in modules, f"{module} no longer exists"


def test_each_allowlisted_name_is_still_defined_in_a_domain_module() -> None:
    defined = {
        f"{module}.{name}"
        for module, path in _modules(ROOT)
        if module.startswith("nextron.domain.")
        for name in _defined_names(parse(path).body)
    }
    for entry in PLATFORM_NAME_ALLOWLIST:
        assert entry.startswith("nextron.domain."), entry
        assert entry in defined, f"{entry} no longer exists"
        assert PLATFORM_WORD.search(entry.rsplit(".", 1)[1]), entry


def test_each_allowlisted_host_literal_is_still_in_its_domain_module() -> None:
    modules = dict(_modules(ROOT))
    for module, literal in HOST_LITERAL_ALLOWLIST:
        assert module.startswith("nextron.domain."), module
        assert module in modules, f"{module} no longer exists"
        assert literal in {text for _, text in _host_literals(modules[module])}, literal


def test_every_approved_extra_port_exists_and_none_is_in_ad9() -> None:
    defined = set()
    for name, path in _modules(ROOT):
        if name.startswith("nextron.ports."):
            tree = parse(path)
            defined |= {
                node.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef) and any(map(_is_protocol_base, node.bases))
            }
    assert APPROVED_EXTRA_PORTS.keys() <= defined
    assert AD9_PORTS.isdisjoint(APPROVED_EXTRA_PORTS)


# Negative probes: each check catches a planted violation in a synthetic tree -------------------


def _plant(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    for parent in path.relative_to(root).parents:
        if parent != Path(".") and not (root / parent / "__init__.py").exists():
            (root / parent / "__init__.py").write_text("", encoding="utf-8")
    path.write_text(source, encoding="utf-8")


@pytest.mark.parametrize(
    ("relative", "named"),
    [
        ("nextron/adapters/linkedin/__init__.py", "nextron.adapters.linkedin"),
        ("nextron/adapters/instagram_feed.py", "nextron.adapters.instagram_feed"),
        ("nextron/app/pipeline/LinkedIn_tasks.py", "nextron.app.pipeline.LinkedIn_tasks"),
        ("nextron/domain/instagram.py", "nextron.domain.instagram"),  # domain needs allowlisting
    ],
)
def test_a_planted_module_is_named(tmp_path: Path, relative: str, named: str) -> None:
    _plant(tmp_path, relative, "")

    assert module_name_problems(tmp_path, {}) == [
        f"{named}: a module named for LinkedIn or Instagram"
    ]


@pytest.mark.parametrize(
    "source",
    [
        "def fetch_linkedin_profile(url):\n    return url\n",
        "class InstagramClient:\n    pass\n",
        "LINKEDIN_LOGIN = 'x'\n",
        "type LinkedInUrl = str\n",
        "a, instagram_token = 1, 2\n",
        # under module-level compound statements
        "if True:\n    def post_to_linkedin():\n        pass\n",
        "try:\n    X = 1\nexcept ImportError:\n    INSTAGRAM_API = None\n",
        "try:\n    pass\nfinally:\n    linkedin_session = None\n",
        "with open('x') as f:\n    LINKEDIN = f\n",
        "for i in range(1):\n    instagram = i\n",
        "while False:\n    linkedin = 1\n",
        # methods and nested classes
        "class Crawler:\n    def fetch_linkedin(self):\n        pass\n",
        "class Crawler:\n    async def instagram_feed(self):\n        pass\n",
        "class Outer:\n    class LinkedInSession:\n        pass\n",
        # import aliases
        "import httpx as linkedin_http\n",
        "from x import client as InstagramClient\n",
    ],
)
def test_a_planted_defined_name_is_caught(tmp_path: Path, source: str) -> None:
    _plant(tmp_path, "nextron/app/research/helpers.py", source)

    [problem] = top_level_name_problems(tmp_path, PLATFORM_NAME_ALLOWLIST)
    assert problem.startswith("nextron.app.research.helpers.")


def test_function_bodies_and_class_fields_are_not_read(tmp_path: Path) -> None:
    _plant(
        tmp_path,
        "nextron/app/research/helpers.py",
        "def f():\n    linkedin = 1\n    return linkedin\n\n\nclass C:\n    linkedin_url: str\n",
    )

    assert top_level_name_problems(tmp_path, PLATFORM_NAME_ALLOWLIST) == []


def test_the_name_allowlist_covers_one_name_not_its_module(tmp_path: Path) -> None:
    _plant(
        tmp_path,
        "nextron/domain/identity.py",
        "def normalize_linkedin_url(text):\n    return text\n\n\ndef fetch_linkedin():\n"
        "    pass\n",
    )

    assert top_level_name_problems(tmp_path, PLATFORM_NAME_ALLOWLIST) == [
        "nextron.domain.identity.fetch_linkedin: named for LinkedIn or Instagram outside the "
        "allowlist"
    ]


def test_a_planted_port_is_caught(tmp_path: Path) -> None:
    _plant(
        tmp_path,
        "nextron/ports/linkedin.py",
        "from typing import Protocol\n\n\nclass LinkedInPort(Protocol):\n"
        "    def send_invitation(self, url: str) -> None: ...\n",
    )
    _plant(
        tmp_path,
        "nextron/ports/other.py",
        "import typing\n\n\nclass CrawlerPort(typing.Protocol):\n    pass\n\n\n"
        "class ScraperPort(typing.Protocol[int]):\n    pass\n",
    )

    assert port_problems(tmp_path, AD9_PORTS | APPROVED_EXTRA_PORTS.keys()) == [
        "nextron.ports.linkedin.LinkedInPort: a port outside AD-9 and the approved extras",
        "nextron.ports.other.ScraperPort: a port outside AD-9 and the approved extras",
    ]


def test_a_planted_job_type_is_caught() -> None:
    names = (*_job_type_names(), "post_to_linkedin", "fetch_Instagram_feed")

    assert job_type_problems(names, PLATFORM_JOB_TYPE_ALLOWLIST) == [
        "post_to_linkedin: a job type named for LinkedIn or Instagram",
        "fetch_Instagram_feed: a job type named for LinkedIn or Instagram",
    ]
    assert job_type_problems(("post_to_linkedin",), {"post_to_linkedin": "probe"}) == []


@pytest.mark.parametrize(
    "source",
    [
        "URL = 'https://www.linkedin.com/in/x'\n",
        "def f(slug):\n    return f'https://lnkd.in/{slug}'\n",
        "BADGE = 'https://static.licdn.com/sc/h/badge.js'\n",
        "EMBED = 'https://WWW.INSTAGRAM.COM/embed.js'\n",
        "SHORT = 'instagr.am/p/x'\n",
        "CDN = 'scontent.cdninstagram.com'\n",
        "class C:\n    host = 'uk.linkedin.com'\n",
    ],
)
def test_a_planted_host_literal_is_caught(tmp_path: Path, source: str) -> None:
    _plant(tmp_path, "nextron/app/research/hosts.py", source)

    [problem] = host_literal_problems(tmp_path, HOST_LITERAL_ALLOWLIST)
    assert problem.startswith("nextron.app.research.hosts (l.")


@pytest.mark.parametrize(
    "lookalike",
    [
        "notlinkedin.company.example",
        "linkedin.company.example",
        "lnkd.info",
        "https://lnkd.in-house.example/",
        "instagram.community",
        "linkedin.com.example.org",
        "myinstagr.am",
    ],
)
def test_a_host_in_a_docstring_or_a_lookalike_is_not_a_problem(
    tmp_path: Path, lookalike: str
) -> None:
    _plant(
        tmp_path,
        "nextron/app/research/hosts.py",
        '"""Never fetches linkedin.com."""\n\n\ndef f():\n    """Nor lnkd.in."""\n'
        f"    return {lookalike!r}\n",
    )

    assert host_literal_problems(tmp_path, HOST_LITERAL_ALLOWLIST) == []


@pytest.mark.parametrize(
    "text",
    ["linkedin.com", "linkedin.com.", "https://lnkd.in/x", "instagram.com:443", "licdn.com?"],
)
def test_a_host_at_the_end_or_before_a_path_port_or_query_is_caught(text: str) -> None:
    assert PLATFORM_HOST.search(text)


def test_the_host_allowlist_covers_one_literal_not_its_module(tmp_path: Path) -> None:
    _plant(
        tmp_path,
        "nextron/domain/fetch_policy.py",
        "DENIED = ('linkedin.com',)\nBADGE = 'https://platform.linkedin.com/badge.js'\n",
    )

    assert host_literal_problems(tmp_path, HOST_LITERAL_ALLOWLIST) == [
        "nextron.domain.fetch_policy (l.2): a LinkedIn or Instagram host"
    ]


@pytest.mark.parametrize(
    "relative",
    [
        "nextron/web/templates/lead.html",
        "nextron/web/static/app.css",
        "nextron/web/static/badge.js",
    ],
)
def test_a_planted_host_in_a_template_or_static_file_is_caught(
    tmp_path: Path, relative: str
) -> None:
    _plant(tmp_path, "nextron/__init__.py", "")
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<script src="https://platform.linkedin.com/badges/js/profile.js"></script>',
        encoding="utf-8",
    )
    (path.parent / "htmx-2.0.11.min.js").write_text("// instagram.com", encoding="utf-8")

    assert host_literal_problems(tmp_path, {}) == [
        f"{relative}: a LinkedIn or Instagram host in a non-Python file"
    ]
