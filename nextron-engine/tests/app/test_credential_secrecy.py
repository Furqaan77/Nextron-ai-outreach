"""The planted-secret test (FR-56, NFR-18, AD-12, AD-17): a credential saved on the Credentials
page and used by an adapter appears in no HTTP response, no log record and no database column.

On a dashboard with real units of work (a database at head), a planted value is saved for
``hunter`` through ``POST /credentials/save``; the page is read; a fake adapter reads the value
with ``read_credential`` and sends it as a bearer header to a fake transport, which records it
and fails, so the adapter logs and raises ``AdapterError``; then four refused requests carry the
value (too long, a refused CSRF token, a malformed body, a repeated ``value``); and the value is
removed in two steps. The checks then look for the value:

- in every HTTP response: status, header names and values, body;
- in every captured log record, before redaction (no ``configure_logging`` runs here, so
  nothing is masked): the message, the args, every attribute and the formatted exception;
- in every value of every column of every table ``sqlite_master`` lists (``jobs.payload`` too,
  once Story 1.10 adds it), and in the bytes of every file under the test's folder.

The planted value is 20 letters and digits that no redaction rule masks, so only its absence
passes. The last tests prove each check finds the value where it is.
"""

import logging
import sqlite3
from collections.abc import Iterable
from pathlib import Path

import pytest
from sqlalchemy import Engine

from nextron.db.uow import UnitOfWorkFactory
from nextron.domain.credentials import CredentialKey
from nextron.domain.redaction import redact
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import SecretsPort, read_credential
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard, form

PLANTED = "Qm7Zr2Vx9Lk4Tw8Np3Hs"
ULID = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
SAVE, REMOVE = "/credentials/save", "/credentials/remove"

log = logging.getLogger("tests.app.fake_hunter")


class FakeTransport:
    """An HTTP transport that records each request's headers, then fails like a dropped
    connection."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send(self, headers: dict[str, str]) -> None:
        self.sent.append(dict(headers))
        raise ConnectionError("connection reset by peer")


class FakeHunterAdapter:
    """How a later adapter uses a credential: read with ``read_credential`` at call time, sent,
    never kept, and never in a log record or an error."""

    def __init__(self, secrets: SecretsPort, transport: FakeTransport) -> None:
        self._secrets = secrets
        self._transport = transport

    def find_email(self, domain_id: str) -> None:
        api_key = read_credential(self._secrets, CredentialKey.HUNTER)
        try:
            self._transport.send({"Authorization": f"Bearer {api_key}"})
        except ConnectionError as exc:
            log.warning(
                "hunter_call_failed",
                extra={"domain_id": domain_id, "code": "transport_failed"},
                exc_info=True,
            )
            raise AdapterError(True, "transport_failed", "the Hunter request failed") from exc


# The checks -------------------------------------------------------------------------------------


def response_leaks(replies: Iterable[Reply], value: str) -> list[str]:
    """Each response that carries ``value`` in its status, headers or body."""
    needle = value.encode()
    problems = []
    for index, reply in enumerate(replies):
        if value in str(reply.status):
            problems.append(f"response {index}: status")
        for name, header in reply.headers:
            if needle in name or needle in header:
                problems.append(f"response {index}: header {name!r}")
        if needle in reply.body:
            problems.append(f"response {index}: body")
    return problems


def record_texts(record: logging.LogRecord) -> list[str]:
    """Every text a log record holds, unredacted."""
    texts = [record.getMessage(), repr(record.args)]
    texts += [f"{name}={attribute!r}" for name, attribute in vars(record).items()]
    if record.exc_info:
        texts.append(logging.Formatter().formatException(record.exc_info))
    if record.stack_info:
        texts.append(record.stack_info)
    return texts


def log_leaks(records: Iterable[logging.LogRecord], value: str) -> list[str]:
    """Each log record that carries ``value`` anywhere."""
    return [
        f"{record.name}: {record.msg!r}"
        for record in records
        if any(value in text for text in record_texts(record))
    ]


def database_leaks(engine: Engine, value: str) -> tuple[list[str], list[str]]:
    """The tables ``sqlite_master`` lists, and each ``table.column`` that holds ``value``."""
    assert engine.url.database
    reader = sqlite3.connect(engine.url.database)
    try:
        tables = [
            name
            for (name,) in reader.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ]
        problems = []
        for table in tables:
            cursor = reader.execute(f'SELECT * FROM "{table}"')
            columns = [column[0] for column in cursor.description]
            for row in cursor:
                for column, cell in zip(columns, row, strict=True):
                    text = cell.decode("utf-8", "replace") if isinstance(cell, bytes) else str(cell)
                    if value in text:
                        problems.append(f"{table}.{column}")
        return tables, problems
    finally:
        reader.close()


def file_leaks(folder: Path, value: str) -> list[str]:
    """Each file under ``folder`` whose bytes hold ``value`` as UTF-8 or UTF-16."""
    needles = (value.encode("utf-8"), value.encode("utf-16-le"))
    return [
        path.relative_to(folder).as_posix()
        for path in sorted(folder.rglob("*"))
        if path.is_file() and any(needle in path.read_bytes() for needle in needles)
    ]


# The flow ---------------------------------------------------------------------------------------


def test_the_planted_value_is_one_no_redaction_rule_masks() -> None:
    assert 15 <= len(PLANTED) <= 25
    assert redact(PLANTED) == PLANTED
    assert redact(f"value {PLANTED} here") == f"value {PLANTED} here"


def test_a_planted_credential_appears_in_no_response_log_record_or_database_column(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    dashboard = Dashboard(units=units)
    cookie = dashboard.open_session()
    token = dashboard.csrf_token(cookie)
    replies: list[Reply] = []

    def post(path: str, body: bytes, *, header_token: bool = True) -> Reply:
        replies.append(
            dashboard.post(
                path,
                cookie=cookie,
                token=token if header_token else None,
                headers=[(b"content-type", FORM)],
                body=body,
            )
        )
        return replies[-1]

    def get(path: str) -> Reply:
        replies.append(dashboard.get(path, cookie=cookie))
        return replies[-1]

    # 1. Save it on the page, then read the page.
    saved = post(SAVE, form(csrf_token=token, credential="hunter", value=PLANTED))
    shown = get("/credentials")

    assert saved.status == 200 and "Saved." in saved.text
    assert shown.status == 200 and "Set, last updated" in shown.text
    entry = (dashboard.secrets.service, "hunter/api_key")
    assert dashboard.secrets.store[entry] == PLANTED  # it really is in Credential Manager

    # 2. An adapter uses it: read at call time, sent as a bearer header, the call fails.
    transport = FakeTransport()
    with pytest.raises(AdapterError) as raised:
        FakeHunterAdapter(dashboard.secrets, transport).find_email(ULID)

    assert transport.sent == [{"Authorization": f"Bearer {PLANTED}"}]
    assert (raised.value.code, raised.value.message) == (
        "transport_failed",
        "the Hunter request failed",
    )
    assert [r.getMessage() for r in caplog.records if r.name == log.name] == ["hunter_call_failed"]

    # 3. Refused requests that carry it.
    too_long = post(SAVE, form(csrf_token=token, credential="hunter", value=PLANTED + "x" * 1300))
    csrf_refused = post(
        SAVE, form(csrf_token="wrong", credential="hunter", value=PLANTED), header_token=False
    )
    malformed = post(  # the value as a field without "="
        SAVE, form(csrf_token=token, credential="hunter", value="x") + b"&" + PLANTED.encode()
    )
    repeated = post(
        SAVE,
        form(csrf_token=token, credential="hunter", value=PLANTED) + b"&value=" + PLANTED.encode(),
    )

    assert too_long.status == 200 and "secret exceeds 1,280 characters" in too_long.text
    assert csrf_refused.status == 403
    assert malformed.status == 400
    assert repeated.status == 400
    assert repeated.body == b"Request refused: malformed form"
    assert dashboard.secrets.store[entry] == PLANTED  # the refusals changed nothing

    # 4. Remove it in two steps.
    asked = post(REMOVE, form(csrf_token=token, credential="hunter"))
    removed = post(REMOVE, form(csrf_token=token, credential="hunter", confirm="yes"))
    get("/credentials")

    assert "Remove the Hunter API key from Windows Credential Manager?" in asked.text
    assert "Removed." in removed.text and "Not set" in removed.text
    assert PLANTED not in dashboard.secrets.store.values()

    # The checks.
    assert response_leaks(replies, PLANTED) == []
    assert log_leaks(caplog.records, PLANTED) == []
    tables, column_leaks = database_leaks(db_engine, PLANTED)
    assert {"alembic_version", "events", "settings"} <= set(tables)
    assert column_leaks == []
    assert (tmp_path / "db" / "nextron.db").is_file()
    assert file_leaks(tmp_path, PLANTED) == []


# Each check finds the value where it is ----------------------------------------------------------


def test_the_response_check_finds_the_value_in_a_status_header_or_body() -> None:
    replies = [
        Reply(200, [(b"x-debug", f"Bearer {PLANTED}".encode())], b""),
        Reply(200, [], f"<p>{PLANTED}</p>".encode()),
        Reply(200, [], b"clean"),
    ]

    assert response_leaks(replies, PLANTED) == ["response 0: header b'x-debug'", "response 1: body"]


@pytest.mark.parametrize("where", ["message", "args", "extra", "exception"])
def test_the_log_check_finds_the_value_before_redaction(
    where: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    probe = logging.getLogger("tests.app.probe")
    if where == "message":
        probe.info(f"Bearer {PLANTED}")
    elif where == "args":
        probe.info("sent %s", PLANTED)
    elif where == "extra":
        probe.info("sent", extra={"header": f"Bearer {PLANTED}"})
    else:
        try:
            raise ConnectionError(f"failed with {PLANTED}")
        except ConnectionError:
            probe.warning("sent", exc_info=True)

    assert len(log_leaks(caplog.records, PLANTED)) == 1


def test_the_database_and_file_checks_find_the_value(
    db_engine: Engine, tmp_path: Path
) -> None:
    assert db_engine.url.database
    writer = sqlite3.connect(db_engine.url.database, isolation_level=None)
    try:
        writer.execute(
            "INSERT INTO settings (id, key, value, updated_at) VALUES (?, ?, ?, ?)",
            (ULID, "sender_timezone", PLANTED, "2026-11-17T09:00:00.000000Z"),
        )
    finally:
        writer.close()
    (tmp_path / "note.txt").write_bytes(PLANTED.encode("utf-16-le"))

    assert database_leaks(db_engine, PLANTED)[1] == ["settings.value"]
    assert "note.txt" in file_leaks(tmp_path, PLANTED)
