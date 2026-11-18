"""Build the dashboard with fakes and open a session on it, as ``nextron open`` would.

``Dashboard`` reads through ``FakeUnits`` unless a test passes real units of work (the
``units`` fixture); its ``Setup``, ``Campaigns`` (``campaigns``, Story 4.2) and ``CompanyFacts``
(``facts``, Story 4.3) use the dashboard's frozen clock, and its ``Credentials`` the dashboard's
in-memory secrets and that clock. ``post``
sends a POST from the dashboard's own origin by default; ``csrf_token(cookie)`` reads the
session's CSRF token from ``GET /``'s ``<body hx-headers>``, as HTMX would; ``form(**fields)``
builds an urlencoded body.

Its ``GoogleAccess`` (``google``) uses the same secrets and clock with fakes:
``FakeGoogleOAuth`` (``oauth``), ``FakeClientFiles`` (``files``), a ``RecordingBrowser``
(``browser``, which records each URL and answers ``opens``), the ``allowlist`` field (a list,
read on every check; ``allowlist_error`` makes the provider raise), and ``listen``, which builds
a ``FakeConsentListener`` per flow and keeps it in ``listeners`` (set ``next_answer`` before the
Grant click, or the listener's ``answer`` before running the job). A flow's background job is
never started on its own: ``start`` keeps it in ``jobs``, and ``run_jobs()`` runs them, so a
test sees the waiting row before Google answers.

Its ``Notify`` (``notify``) uses the same clock, a ``JobQueue`` (``queue``) on the real job types
(``job_types``, from ``nextron.main.build_job_types``) and, for Telegram, the ``alert`` port
(``InMemoryAlert`` unless a test passes another ``AlertPort``); the notification area and its
Dismiss go through it, on the dashboard's units of work.

Its ``Budget`` (``budget``) reads ``meter``, an ``InMemoryUsageMeter`` on the dashboard's clock,
and raises its notices through that ``Notify``; the Quotas and cost page reads it. The page
reads the settings, so it needs real units of work (``Dashboard(units=units)``).

Its ``EngineHealth`` (``health``) uses the same clock and job types, the real health-issue
registry (``nextron.main.build_health_issues(budget=budget)``) and ``readiness``, a callable
returning a fixed list (``READINESS``, the thirteen doctor checks, all ``ok``) unless a test
passes another; the health panel and the notification area's error count read it, and the
panel's Dismiss goes through ``queue``. They read the database, so they need real units of
work too.

Its ``Backups`` (``backups``, Story 1.16) uses the same clock, ``notify`` and ``queue``, and a
``LocalBackups`` (``local``) in ``<%LOCALAPPDATA%>\\Nextron\\backups\\test\\local`` (the
test's temporary folder) whose ``snapshot`` is ``snapshot`` (a test passes
``functools.partial(nextron.db.repos.ops.snapshot.snapshot, <db file>)`` to take real copies;
by default a copy fails with ``RuntimeError``). The panel's "Back up now" queues its job.
Its ``OffpcBackups`` (``offpc``, Story 1.17) sends verified copies to ``offpc_target``, an
``InMemoryBackupTarget`` whatever the folder, with the data dir
``<%LOCALAPPDATA%>\\Nextron\\test`` and the backup dir that holds ``local_dir``.
Its ``RestoreTests`` (``restore_tests``, Story 1.18) uses the same clock, ``offpc``, ``notify``
and ``queue``, that backup dir and ``inspect`` (``nextron.db.repos.ops.snapshot.inspect_copy``
unless a test passes another). The panel's "Run restore test now" queues its job.

Its ``LeadImports`` (``lead_imports``, Story 2.3) uses the same clock, a ``Discovery`` and a
``Research`` on it, and ``email_syntax`` (a ``FakeEmailSyntax`` unless a test passes another);
the CSV import page runs it, so it needs real units of work too. Its ``ContactMarkets``
(``contact_markets``, Story 3.2) uses the same clock, that ``Research`` and ``queue``.
``upload(**texts, file=...)`` builds a ``multipart/form-data`` body as a browser would, with its
``Content-Type``.

Its ``Playbook`` (``playbook``, Story 4.4) uses the same clock; the AI content view's "Check
for changes" runs it on what ``ai_content`` loads (``shipped_ai_content`` unless a test passes
another loader: the shipped prompt files and defaults, and ``TEST_PLAYBOOK``, a synthetic
Sales Playbook, so no test reads or edits the real one).

Its ``Erasures`` (``erasures``, Story 3.13) is ``Erasures(clock, Compliance(clock),
queue=queue)`` (Story 3.14: an erasure enqueues its ``erase_external`` jobs on the dashboard's
queue, which nothing runs here); the Lead page's erase step runs it, on real units of work.

Its ``Sending`` (``sending``, Story 4.7) is ``Sending(clock, env=env)``; the Sending Inboxes panel
registers through it. The default ``env`` is ``dev``, so a panel test that registers an inbox
saves ``sink_allowlist`` (and ``sender_name``) through ``setup.save`` first, on real units of
work.
"""

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI

from nextron import main
from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.discovery.api import Discovery, LeadImports
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.ops.api import (
    Backups,
    Budget,
    DashboardEntry,
    EngineHealth,
    LocalBackups,
    OffpcBackups,
    RestoreTests,
)
from nextron.app.pipeline.api import Pipeline
from nextron.app.playbook.api import CompanyFacts, Playbook
from nextron.app.research.api import ContactMarkets, Research
from nextron.app.research.extraction import LeadSignals
from nextron.app.sending.api import Sending
from nextron.app.setup.api import Campaigns, Credentials, GoogleAccess, Setup
from nextron.db.repos.ops.snapshot import CopyInspection, Snapshot, inspect_copy
from nextron.domain.ai import RouteSpec
from nextron.domain.alert import Channel
from nextron.domain.job import JobTypes
from nextron.domain.playbook import PlaybookProblem, PlaybookSnapshot, read_playbook
from nextron.domain.prompts import PromptFile
from nextron.domain.readiness import CheckResult, Status
from nextron.ports.alert import AlertPort
from nextron.ports.email_syntax import EmailSyntaxPort
from nextron.ports.google_oauth import Query
from nextron.web.app import create_app
from nextron.web.routing import Units
from tests.asgi import DEFAULT_HOST, Headers, Reply, request
from tests.fakes.alert import InMemoryAlert
from tests.fakes.backup_target import InMemoryBackupTarget
from tests.fakes.clock import FrozenClock
from tests.fakes.email_syntax import FakeEmailSyntax
from tests.fakes.google_oauth import FakeClientFiles, FakeConsentListener, FakeGoogleOAuth
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.units import FakeUnits
from tests.fakes.usage_meter import InMemoryUsageMeter

PORT = 8766  # matches tests.asgi.DEFAULT_HOST
ORIGIN = b"http://" + DEFAULT_HOST
FORM = b"application/x-www-form-urlencoded"
READINESS = tuple(
    CheckResult(check_id, Status.OK, f"{check_id} is fine")
    for check_id in (
        "python_version",
        "sqlite_version",
        "tzdata",
        "prod_env_not_persistent",
        "data_dir_outside_onedrive",
        "backup_dir_separate",
        "port_free",
        "required_credentials",
        "internal_notify_grant",
        "offpc_backup",
        "required_settings",
        "database_revision",
        "unsub_token_keys",
    )
)


TEST_PLAYBOOK = "---\ntitle: Test Sales Playbook\nversion: 9.9.9-test\n---\n\n# Test Playbook\n"


def shipped_ai_content() -> tuple[
    tuple[PromptFile, ...], tuple[RouteSpec, ...], PlaybookSnapshot | PlaybookProblem
]:
    """The shipped prompt files and defaults, and the synthetic ``TEST_PLAYBOOK``."""
    files, defaults = main.load_prompts()
    return files, defaults, read_playbook(TEST_PLAYBOOK.encode("utf-8"))


def no_copies(target: Path) -> Snapshot:
    """The dashboard's default snapshot: it takes no copies."""
    raise RuntimeError("this dashboard takes no copies")


def no_recovery() -> Any:
    """The dashboard's ``check_recovery`` runner: no job runs here."""
    raise RuntimeError("this dashboard runs no check_recovery job")


def no_crawl_jobs() -> Any:
    """``build_job_types``'s ``crawls``, ``discovery``, ``research`` and ``extractions``: the
    dashboard runs no Story 2.5 or 2.9 job."""
    raise AssertionError(
        "the dashboard runs no enrich_lead, canonicalize_lead, purge_page_text or extract_lead"
    )


def default_data_dir() -> Path:
    """``<%LOCALAPPDATA%>\\Nextron\\test``: under the test's temporary folder."""
    return Path(os.environ["LOCALAPPDATA"]) / "Nextron" / "test"


def default_local_dir() -> Path:
    """``<%LOCALAPPDATA%>\\Nextron\\backups\\test\\local``: under the test's temporary
    folder."""
    return Path(os.environ["LOCALAPPDATA"]) / "Nextron" / "backups" / "test" / "local"


UPLOAD_BOUNDARY = "----NextronTestBoundary7MA4YWxkTrZu0gW"


def upload(
    file: bytes | None = None, *, filename: str = "leads.csv", **texts: str
) -> tuple[bytes, Headers]:
    """A ``multipart/form-data`` body of ``texts`` (in order) then, unless ``file`` is
    ``None``, the file part ``file``, as a browser encodes them; with its ``Content-Type``
    header, to pass as ``headers``."""
    lines: list[bytes] = []
    for name, value in texts.items():
        lines += [
            f"--{UPLOAD_BOUNDARY}".encode(),
            f'Content-Disposition: form-data; name="{name}"'.encode(),
            b"",
            value.encode("utf-8"),
        ]
    if file is not None:
        lines += [
            f"--{UPLOAD_BOUNDARY}".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode(),
            b"Content-Type: text/csv",
            b"",
            file,
        ]
    lines.append(f"--{UPLOAD_BOUNDARY}--".encode())
    body = b"\r\n".join(lines) + b"\r\n"
    content_type = f"multipart/form-data; boundary={UPLOAD_BOUNDARY}".encode()
    return body, [(b"content-type", content_type)]


def form(**fields: str) -> bytes:
    """An ``application/x-www-form-urlencoded`` body of ``fields``, in order, as a browser
    encodes it."""
    return urlencode(fields).encode("ascii")


class _BodyHeaders(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body":
            self.values.append(dict(attrs).get("hx-headers"))


def body_hx_headers(html: str) -> dict[str, object]:
    """The JSON object in the page's one ``<body hx-headers>``."""
    parser = _BodyHeaders()
    parser.feed(html)
    parser.close()
    [value] = parser.values
    assert value is not None, "<body> has no hx-headers"
    headers = json.loads(value)
    assert isinstance(headers, dict)
    return headers


class RecordingBrowser:
    """A browser that records each URL it is asked to open and answers ``opens``; with
    ``error`` set it raises that instead."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.opens = True
        self.error: Exception | None = None

    def __call__(self, url: str) -> bool:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return self.opens


@dataclass
class Dashboard:
    """A dashboard app on ``127.0.0.1:8766`` with in-memory secrets, a frozen clock and, by
    default, fake units of work."""

    secrets: InMemorySecrets = field(default_factory=InMemorySecrets)
    clock: FrozenClock = field(default_factory=FrozenClock)
    env: str = "dev"
    version: str = "1.2.3"
    monotonic: Callable[[], float] | None = None
    units: Units = field(default_factory=FakeUnits)
    oauth: FakeGoogleOAuth = field(default_factory=FakeGoogleOAuth)
    files: FakeClientFiles = field(default_factory=FakeClientFiles)
    browser: RecordingBrowser = field(default_factory=RecordingBrowser)
    allowlist: list[str] = field(default_factory=list)
    allowlist_error: Exception | None = None
    next_answer: Query | None = None  # what the next listener's wait returns
    listen_error: Exception | None = None
    listeners: list[FakeConsentListener] = field(default_factory=list)
    jobs: list[Callable[[], None]] = field(default_factory=list)
    alert: AlertPort = field(default_factory=InMemoryAlert)
    meter: InMemoryUsageMeter | None = None  # on the dashboard's clock unless a test passes one
    readiness: Callable[[], Sequence[CheckResult]] = lambda: READINESS
    snapshot: Callable[[Path], Snapshot] = no_copies
    local_dir: Path = field(default_factory=default_local_dir)
    offpc_target: InMemoryBackupTarget = field(default_factory=InMemoryBackupTarget)
    inspect: Callable[[Path], CopyInspection] = inspect_copy
    email_syntax: EmailSyntaxPort = field(default_factory=FakeEmailSyntax)
    ai_content: Callable[
        [], tuple[Sequence[PromptFile], Sequence[RouteSpec], PlaybookSnapshot | PlaybookProblem]
    ] = shipped_ai_content
    app: FastAPI = field(init=False)
    entry: DashboardEntry = field(init=False)
    setup: Setup = field(init=False)
    campaigns: Campaigns = field(init=False)
    facts: CompanyFacts = field(init=False)
    playbook: Playbook = field(init=False)
    credentials: Credentials = field(init=False)
    google: GoogleAccess = field(init=False)
    job_types: JobTypes = field(init=False)
    queue: JobQueue = field(init=False)
    notify: Notify = field(init=False)
    budget: Budget = field(init=False)
    health: EngineHealth = field(init=False)
    local: LocalBackups = field(init=False)
    offpc: OffpcBackups = field(init=False)
    backups: Backups = field(init=False)
    restore_tests: RestoreTests = field(init=False)
    lead_imports: LeadImports = field(init=False)
    sending: Sending = field(init=False)
    research: Research = field(init=False)
    contact_markets: ContactMarkets = field(init=False)
    erasures: Erasures = field(init=False)

    def __post_init__(self) -> None:
        self.entry = DashboardEntry(self.secrets, self.clock)
        self.setup = Setup(self.clock)
        self.campaigns = Campaigns(self.clock)
        self.facts = CompanyFacts(self.clock)
        self.playbook = Playbook(self.clock)
        self.credentials = Credentials(self.secrets, self.clock)
        self.google = GoogleAccess(
            self.secrets,
            self.clock,
            oauth=self.oauth,
            listen=self.listen,
            files=self.files,
            browser=self.browser,
            env=self.env,
            allowlist=self.read_allowlist,
            start=self.jobs.append,
        )
        self.job_types = main.build_job_types(
            alerts=lambda: self.notify,
            backups=lambda: self.backups,
            restore_tests=lambda: self.restore_tests,
            recovery=no_recovery,
            contact_markets=lambda: self.contact_markets,
            crawls=no_crawl_jobs,
            discovery=no_crawl_jobs,
            research=no_crawl_jobs,
            extractions=no_crawl_jobs,
        )
        self.queue = JobQueue(self.clock, self.job_types)
        self.notify = Notify(
            self.clock, self.queue, alert_ports={Channel.TELEGRAM: lambda: self.alert}
        )
        self.local = LocalBackups(
            self.clock, env="test", local_dir=self.local_dir, snapshot=self.snapshot
        )
        self.offpc = OffpcBackups(
            self.clock,
            env="test",
            data_dir=default_data_dir(),
            backup_dir=self.local_dir.parent,
            targets=lambda folder: self.offpc_target,
        )
        self.backups = Backups(
            self.clock, self.local, notify=self.notify, queue=self.queue, offpc=self.offpc
        )
        self.restore_tests = RestoreTests(
            self.clock,
            env="test",
            backup_dir=self.local_dir.parent,
            offpc=self.offpc,
            inspect=self.inspect,
            notify=self.notify,
            queue=self.queue,
        )
        self.sending = Sending(self.clock, env=self.env)
        meter = self.meter if self.meter is not None else InMemoryUsageMeter(self.clock)
        self.meter = meter
        self.budget = Budget(self.clock, meter, self.notify)
        self.health = EngineHealth(
            self.clock,
            self.job_types,
            issues=main.build_health_issues(budget=self.budget),
            readiness=self.readiness,
        )
        self.research = Research(self.clock)
        self.contact_markets = ContactMarkets(self.clock, self.research, self.queue)
        self.lead_imports = LeadImports(
            self.clock,
            Discovery(self.clock, Pipeline(self.clock), self.queue),
            self.research,
            self.email_syntax,
        )
        self.erasures = Erasures(self.clock, Compliance(self.clock), queue=self.queue)
        extra = {"monotonic": self.monotonic} if self.monotonic is not None else {}
        self.app = create_app(
            env=self.env,
            version=self.version,
            port=PORT,
            entry=self.entry,
            setup=self.setup,
            campaigns=self.campaigns,
            facts=self.facts,
            playbook=self.playbook,
            ai_content=self.ai_content,
            credentials=self.credentials,
            google=self.google,
            notify=self.notify,
            budget=self.budget,
            health=self.health,
            queue=self.queue,
            backups=self.backups,
            restore_tests=self.restore_tests,
            lead_imports=self.lead_imports,
            sending=self.sending,
            lead_signals=LeadSignals(self.clock),
            erasures=self.erasures,
            units=self.units,
            **extra,
        )

    def listen(self) -> FakeConsentListener:
        if self.listen_error is not None:
            raise self.listen_error
        self.listeners.append(FakeConsentListener(self.next_answer))
        return self.listeners[-1]

    def read_allowlist(self) -> list[str]:
        if self.allowlist_error is not None:
            raise self.allowlist_error
        return list(self.allowlist)

    def run_jobs(self) -> int:
        """Run every queued flow job, in order; return how many ran."""
        jobs, self.jobs[:] = list(self.jobs), []
        for job in jobs:
            job()
        return len(jobs)

    def enter(self, code: str) -> Reply:
        return request(self.app, "/enter", query=b"t=" + code.encode())

    def open_session(self) -> bytes:
        """Issue a code, enter with it and return the ``Cookie`` header value to send."""
        reply = self.enter(self.entry.issue_code())
        assert reply.status == 303, reply
        cookie = reply.header(b"set-cookie")
        assert cookie is not None
        return cookie.split(b";", 1)[0]

    def get(self, path: str, *, cookie: bytes | None = None, query: bytes = b"") -> Reply:
        headers = [(b"host", DEFAULT_HOST)]
        if cookie is not None:
            headers.append((b"cookie", cookie))
        return request(self.app, path, query=query, headers=headers)

    def csrf_token(self, cookie: bytes) -> str:
        """The session's CSRF token, read from ``GET /``'s ``<body hx-headers>``."""
        reply = self.get("/", cookie=cookie)
        assert reply.status == 200, reply
        token = body_hx_headers(reply.text)["X-CSRF-Token"]
        assert isinstance(token, str) and token
        return token

    def post(
        self,
        path: str,
        *,
        cookie: bytes | None = None,
        token: str | None = None,
        origin: bytes | None = ORIGIN,
        headers: Headers = (),
        body: bytes = b"",
        query: bytes = b"",
        catch: bool = False,
    ) -> Reply:
        """POST from the dashboard's origin. ``token`` goes in an ``X-CSRF-Token`` header;
        ``origin=None`` sends no ``Origin``; ``headers`` are added after these."""
        sent = [(b"host", DEFAULT_HOST)]
        if cookie is not None:
            sent.append((b"cookie", cookie))
        if origin is not None:
            sent.append((b"origin", origin))
        if token is not None:
            sent.append((b"x-csrf-token", token.encode()))
        sent.extend(headers)
        return request(
            self.app, path, method="POST", query=query, headers=sent, body=body, catch=catch
        )
