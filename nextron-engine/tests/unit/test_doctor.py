"""``nextron doctor`` end to end (Story 1.14): every I/O matrix row through ``main.doctor`` with
fakes (``InMemorySecrets``, ``FakeGoogleOAuth``, ``FrozenClock``, ``fake_probes``) and a real
temporary database. Doctor takes no lock, starts nothing, configures no logging and never
creates, migrates or writes the database."""

import io
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

import httpx2
import pytest
from sqlalchemy.exc import OperationalError

from nextron import main
from nextron.adapters.google_oauth import GoogleOAuthClient
from nextron.app.setup.api import Setup
from nextron.bootstrap import ENV_ERROR, load_bootstrap
from nextron.db.engine import create_engine
from nextron.db.migrations import ensure_current, head_revision
from nextron.db.uow import Subscribers, UnitOfWorkFactory
from nextron.domain import credentials
from nextron.domain.backup import ERASURE_LEDGER_FILE
from nextron.domain.budget import ApiCall
from nextron.domain.google_oauth import (
    CLIENT_META_NAME,
    CLIENT_SECRET_NAME,
    ClientConfig,
    GoogleGrant,
    GrantMeta,
    decode_grant_meta,
    encode_client_config,
    encode_grant_meta,
    grant_spec,
)
from nextron.lock import InstanceLock
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import KEYRING_FAILED
from nextron.startup_checks import Probes
from tests.fakes.clock import FrozenClock
from tests.fakes.google_oauth import CLIENT_ID, FakeGoogleOAuth
from tests.fakes.probes import PortProbe, fake_probes
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.unsubscribe import ScriptedUnsubscribe
from tests.migration_chains import run_sql, sha256, stored_revisions

CHECKS = [
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
    "erasure_ledger",  # Story 3.16
    "required_settings",
    "database_revision",
    "unsub_token_keys",
    "unsub_worker",
]
ALL_OK = dict.fromkeys(CHECKS, "ok")

PLANTED_CREDENTIAL = "QZXPLANTEDCREDENTIAL"
PLANTED_REFRESH = "1//QZXPLANTEDREFRESH"
PLANTED_CLIENT_SECRET = "GOCSPX-QZXPLANTEDCLIENTSECRET"
PLANTED_ACCOUNT = "qzxplanted.account@nextronai.com"
PLANTED_ACCESS = "ya29.QZXPLANTEDACCESS"
PLANTED_PASSPHRASE = "QZXPLANTEDBACKUPPASSPHRASE"
PLANTED_K_TOK = "QZXPLANTEDKTOK" + "A" * 28 + "Q"  # 43 base64url characters, a 32-byte key
PLANTED_K_EDGE = "QZXPLANTEDKEDGE" + "A" * 27 + "Q"
PLANTED = (
    PLANTED_CREDENTIAL,
    PLANTED_PASSPHRASE,
    PLANTED_K_TOK,
    PLANTED_K_EDGE,
    PLANTED_REFRESH,
    PLANTED_CLIENT_SECRET,
    PLANTED_ACCOUNT,
    "qzxplanted",
    PLANTED_ACCESS,
)
GRANTED_AT = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
NOTIFY = grant_spec(GoogleGrant.INTERNAL_NOTIFY)
FOOTER_SETTINGS = {  # the required footer settings (Story 4.11)
    "sender_signature": "Furqaan, Nextron AI",
    "legal_name": "Nextron AI Ltd",
    "postal_address": "1 Example Street, London EC1A 1AA",
    "privacy_notice_url": "https://nextronai.com/privacy",
}
SETTINGS = {
    "sender_name": "Furqaan Ahmed",
    "sender_timezone": "Europe/London",
    "alert_chat_id": "-1001234567890",
    **FOOTER_SETTINGS,
}
FOOTER_UNSET = ("sender_signature", "legal_name", "postal_address", "privacy_notice_url")

type Results = dict[str, tuple[str, str]]


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``NEXTRON_ENV=dev`` with a data dir under ``tmp_path`` (not created); no server can
    start."""
    folder = tmp_path / "data" / "dev"
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(folder))

    def no_server(*args: object, **kwargs: object) -> None:
        raise AssertionError("doctor must not start a server")

    monkeypatch.setattr(main.uvicorn, "run", no_server)
    return folder


def other_drive_folder(data_dir: Path) -> str:
    """An off-PC folder on another drive than ``data_dir`` (doctor never touches it)."""
    drive = "Q:" if data_dir.drive.casefold() != "q:" else "R:"
    return f"{drive}\\NextronBackups"


def make_database(
    data_dir: Path, settings: dict[str, str] | None = None, *, offpc: bool = True
) -> Path:
    """``<data_dir>\\nextron.db`` at head holding ``settings`` (both required ones unless
    given) and, with ``offpc``, an off-PC folder on another drive; its engine disposed so the
    WAL is checkpointed into the file."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "nextron.db"
    engine = create_engine(path)
    try:
        ensure_current(engine)
        subscribers = Subscribers()
        subscribers.freeze()
        units = UnitOfWorkFactory(engine, subscribers)
        chosen = dict(SETTINGS if settings is None else settings)
        if offpc:
            chosen["backup_offpc_dir"] = other_drive_folder(data_dir)
        with units.open():
            Setup(FrozenClock()).save(chosen)
    finally:
        engine.dispose()
    ledger = load_bootstrap().backup_dir / ERASURE_LEDGER_FILE  # the engine's first start made it
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.touch()
    return path


def healthy_secrets() -> InMemorySecrets:
    """Every required credential, the Backup passphrase, kid 1's unsubscribe token keys, the
    Google client and the ``internal_notify`` grant."""
    secrets = InMemorySecrets("dev")
    for found in credentials.declared():
        if found.required:
            secrets.write(found.secret_name, f"{PLANTED_CREDENTIAL}{found.key}")
    secrets.write("backup/passphrase", PLANTED_PASSPHRASE)
    secrets.write("unsub/k_tok/1", PLANTED_K_TOK)
    secrets.write("unsub/k_edge/1", PLANTED_K_EDGE)
    secrets.write(
        CLIENT_SECRET_NAME, encode_client_config(ClientConfig(CLIENT_ID, PLANTED_CLIENT_SECRET))
    )
    secrets.write(CLIENT_META_NAME, "2026-11-01T09:00:00.000000Z")
    secrets.write(NOTIFY.secret_name, PLANTED_REFRESH)
    secrets.write(NOTIFY.meta_name, encode_grant_meta(GrantMeta(PLANTED_ACCOUNT, GRANTED_AT)))
    secrets.calls.clear()
    return secrets


@pytest.fixture
def healthy(data_dir: Path) -> Path:
    """A database at head with both required settings; returns its path."""
    return make_database(data_dir)


def doctor_output(
    *,
    as_json: bool = False,
    env: str | None = None,
    probes: Probes | None = None,
    secrets: InMemorySecrets | None = None,
    oauth: Any = None,
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main.doctor(
        env=env,
        as_json=as_json,
        probes=probes if probes is not None else fake_probes(),
        secrets=secrets if secrets is not None else healthy_secrets(),
        google_oauth=oauth if oauth is not None else FakeGoogleOAuth(),
        clock=FrozenClock(),
        out=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


def run_doctor(**kwargs: Any) -> tuple[int, Results]:
    """Doctor's exit code and its JSON results as ``{id: (status, message)}``, in order."""
    code, out, err = doctor_output(as_json=True, **kwargs)
    assert err == ""
    rows = json.loads(out)
    assert [row["id"] for row in rows] == CHECKS
    return code, {row["id"]: (row["status"], row["message"]) for row in rows}


def statuses(results: Results) -> dict[str, str]:
    return {check: status for check, (status, _) in results.items()}


def table_rows(out: str) -> list[tuple[str, str, str]]:
    """The table's ``(status, id, message)`` rows, without the header and the counts."""
    lines = out.splitlines()
    return [tuple(line.split(None, 2)) for line in lines[1:-1]]  # type: ignore[misc]


# Row: All ok ------------------------------------------------------------------------------------


def test_all_ok_prints_fifteen_ok_rows_and_changes_no_database_byte(healthy: Path) -> None:
    before = sha256(healthy)
    oauth = FakeGoogleOAuth()

    code, out, err = doctor_output(oauth=oauth)

    assert (code, err) == (0, "")
    lines = out.splitlines()
    width = len("data_dir_outside_onedrive")
    assert lines[0] == f"status  {'check':<{width}}  message"
    assert [(status, check) for status, check, _ in table_rows(out)] == [
        ("ok", check) for check in CHECKS
    ]
    assert lines[-1] == "15 ok, 0 warn, 0 fail"
    assert len(lines) == 17
    assert out.endswith("\n") and not out.endswith("\n\n")
    assert sha256(healthy) == before
    assert stored_revisions(healthy) == [head_revision()]
    assert oauth.calls == [("refresh", PLANTED_REFRESH)]
    assert sorted(path.name for path in healthy.parent.iterdir()) in (
        ["nextron.db"],
        ["nextron.db", "nextron.db-shm", "nextron.db-wal"],  # decision 5
    )


def test_doctor_never_locks_migrates_logs_or_builds_the_app(
    healthy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(name: str) -> Callable[..., Any]:
        def refused(*args: object, **kwargs: object) -> Any:
            raise AssertionError(f"doctor must not call {name}")

        return refused

    for name in (
        "InstanceLock",
        "ensure_current",
        "open_database",
        "connect_database",
        "create_engine",
        "build_app",
        "Runner",
        "Scheduler",
        "HeartbeatWriter",
        "configure_logging",
    ):
        monkeypatch.setattr(main, name, refuse(name))

    code, results = run_doctor()

    assert code == 0
    assert statuses(results) == ALL_OK
    assert not (healthy.parent / "nextron.lock").exists()
    assert not (healthy.parent / "logs").exists()


def test_a_refresh_through_the_real_client_writes_no_usage_row(
    healthy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real ``GoogleOAuthClient`` wiring, on an ``httpx2.MockTransport``: the one refresh is
    recorded in that run's ``DoctorMeter`` only."""
    requests: list[httpx2.Request] = []
    meters: list[Any] = []

    def google(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "access_token": PLANTED_ACCESS,
                "expires_in": 3599,
                "scope": NOTIFY.scope,
                "token_type": "Bearer",
            },
        )

    def client(*, meter: Any) -> GoogleOAuthClient:
        meters.append(meter)
        return GoogleOAuthClient(
            meter=meter, http=httpx2.Client(transport=httpx2.MockTransport(google))
        )

    monkeypatch.setattr(main, "GoogleOAuthClient", client)
    before = sha256(healthy)
    out, err = io.StringIO(), io.StringIO()

    code = main.doctor(
        probes=fake_probes(), secrets=healthy_secrets(), clock=FrozenClock(), out=out, err=err
    )

    assert code == 0, out.getvalue()
    [meter] = meters
    assert isinstance(meter, main.DoctorMeter)
    assert meter.calls == [ApiCall("google_oauth", "token_refresh", ok=True)]
    assert len(requests) == 1
    assert sha256(healthy) == before
    reader = sqlite3.connect(healthy)
    try:
        assert reader.execute("SELECT count(*) FROM api_usage").fetchone() == (0,)
    finally:
        reader.close()
    assert PLANTED_ACCESS not in out.getvalue() + err.getvalue()


def test_the_doctor_meter_writes_nothing_and_reports_an_empty_month() -> None:
    meter = main.DoctorMeter()
    call = ApiCall("google_oauth", "token_refresh", ok=False, error_code="google_unavailable")

    meter.record(call)

    assert meter.calls == [call]
    usage = meter.snapshot(datetime(2026, 11, 17, 12, 0, tzinfo=UTC))
    assert (usage.month, usage.api, usage.ai_calls, usage.ai_cost_micros) == ("2026-11", (), 0, 0)


# Row: Fresh install -----------------------------------------------------------------------------


def test_a_fresh_install_fails_on_what_it_lacks_and_creates_nothing(data_dir: Path) -> None:
    oauth = FakeGoogleOAuth()

    code, results = run_doctor(secrets=InMemorySecrets("dev"), oauth=oauth)

    assert code == 1
    assert statuses(results) == ALL_OK | {
        "required_credentials": "fail",
        "internal_notify_grant": "fail",
        "offpc_backup": "warn",
        "erasure_ledger": "warn",  # no database yet: the first engine start creates it
        "required_settings": "fail",
        "database_revision": "warn",
        "unsub_token_keys": "fail",
    }
    assert results["offpc_backup"][1] == (
        "off-PC backup not configured (OQ-A3): backups are local only; no off-PC backup folder "
        "is set; set Off-PC backup folder (backup_offpc_dir) on the Settings page; the Backup "
        "passphrase is not set; enter it on the Credentials page"
    )
    assert results["unsub_token_keys"][1] == (  # the schema default kid with no database file
        "unsubscribe token keys of key id 1: unsub/k_tok/1 is not set; unsub/k_edge/1 is not "
        "set; enter them in Unsubscribe token keys on the Credentials page"
    )
    assert results["required_settings"][1] == (
        "required settings not set or no longer valid: Sender's name (sender_name), Sender's "
        "timezone (sender_timezone), Telegram alert chat id (alert_chat_id), Sender signature "
        "(sender_signature), Legal name (legal_name), Postal address (postal_address), "
        "Privacy notice URL (privacy_notice_url); set them on the Settings page"
    )
    assert "will be created and migrated on next start" in results["database_revision"][1]
    assert "not imported" in results["internal_notify_grant"][1]
    assert oauth.calls == []
    assert not data_dir.exists()


# Row: Unsubscribe token keys (Story 3.8) -------------------------------------------------------

UNSUB_FIX = "enter them in Unsubscribe token keys on the Credentials page"


@pytest.mark.parametrize(
    ("name", "value", "problem"),
    [
        ("unsub/k_tok/1", None, "unsub/k_tok/1 is not set"),
        ("unsub/k_tok/1", PLANTED_K_TOK[:-1], "unsub/k_tok/1 is not a 32-byte base64url key"),
        ("unsub/k_edge/1", None, "unsub/k_edge/1 is not set"),
        ("unsub/k_edge/1", "+" * 43, "unsub/k_edge/1 is not a 32-byte base64url key"),
        (
            "unsub/k_edge/1",
            PLANTED_K_TOK,
            "unsub/k_tok/1 and unsub/k_edge/1 are the same key; make a new k_edge",
        ),
    ],
    ids=["k_tok_missing", "k_tok_malformed", "k_edge_missing", "k_edge_malformed", "equal"],
)
def test_a_missing_or_malformed_unsubscribe_key_fails_naming_its_entry(
    healthy: Path, name: str, value: str | None, problem: str
) -> None:
    secrets = healthy_secrets()
    if value is None:
        secrets.delete(name)
    else:
        secrets.store[(secrets.service, name)] = value

    code, results = run_doctor(secrets=secrets)

    assert code == 1
    assert statuses(results) == ALL_OK | {"unsub_token_keys": "fail"}
    assert results["unsub_token_keys"][1] == (
        f"unsubscribe token keys of key id 1: {problem}; {UNSUB_FIX}"
    )
    assert not any(planted in message for _, message in results.values() for planted in PLANTED)


def test_the_active_kid_is_read_from_the_database(data_dir: Path) -> None:
    make_database(data_dir, {**SETTINGS, "unsub_active_kid": "2"})

    code, results = run_doctor()  # only kid 1's keys are stored

    assert code == 1
    assert results["unsub_token_keys"] == (
        "fail",
        "unsubscribe token keys of key id 2: unsub/k_tok/2 is not set; unsub/k_edge/2 is not "
        f"set; {UNSUB_FIX}",
    )


# Row: Behind ------------------------------------------------------------------------------------


def test_a_database_behind_head_warns_and_is_not_migrated(healthy: Path) -> None:
    run_sql(healthy, "UPDATE alembic_version SET version_num = '0005_1_12_notify'")
    before = sha256(healthy)

    code, results = run_doctor()

    assert code == 0  # a warning is not a failure
    assert statuses(results) == ALL_OK | {"database_revision": "warn"}
    assert results["database_revision"][1] == (
        f"the database is at 0005_1_12_notify, behind {head_revision()}; "
        "it will migrate on next start"
    )
    assert stored_revisions(healthy) == ["0005_1_12_notify"]
    assert sha256(healthy) == before


def test_an_empty_database_file_warns(data_dir: Path) -> None:
    data_dir.mkdir(parents=True)
    (data_dir / "nextron.db").write_bytes(b"")

    code, results = run_doctor()

    assert results["database_revision"] == (
        "warn",
        "the database is empty; it will migrate on next start",
    )
    assert (data_dir / "nextron.db").read_bytes() == b""
    assert code == 1  # the settings can't be read from an empty file


# Row: Ahead / foreign ---------------------------------------------------------------------------


def _ahead(path: Path) -> None:
    run_sql(path, "UPDATE alembic_version SET version_num = '0099_future'")


def _foreign_wal(path: Path) -> None:
    path.unlink()
    run_sql(path, "PRAGMA journal_mode=WAL", "CREATE TABLE leads (id INTEGER PRIMARY KEY)")


def _not_wal(path: Path) -> None:
    path.unlink()
    run_sql(path, "CREATE TABLE leads (id INTEGER PRIMARY KEY)")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            _ahead,
            "database revision 0099_future is newer than this code; run the newer code or "
            "restore a backup",
        ),
        (
            _foreign_wal,
            "the database has tables but no Alembic revision; it was not made by this engine",
        ),
        (
            _not_wal,
            "the database file uses journal_mode delete, not wal; it is not this engine's "
            "database",
        ),
    ],
    ids=["ahead", "tables-without-revision", "not-wal"],
)
def test_a_database_ahead_or_foreign_fails_untouched(
    healthy: Path, change: Callable[[Path], None], message: str
) -> None:
    change(healthy)
    before = sha256(healthy)

    code, results = run_doctor()

    assert code == 1
    assert results["database_revision"] == ("fail", message)
    assert sha256(healthy) == before
    reader = sqlite3.connect(healthy)
    try:
        mode = reader.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        reader.close()
    assert mode == ("delete" if change is _not_wal else "wal")


# Row: Port --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "status"),
    [(30.0, "ok"), (300.0, "fail"), (None, "fail")],
    ids=["heartbeat-30s", "heartbeat-300s", "no-heartbeat"],
)
def test_a_busy_port_is_ok_only_while_this_environments_engine_beats(
    healthy: Path, age: float | None, status: str
) -> None:
    asked: list[PurePath] = []

    def file_age(path: PurePath) -> float | None:
        asked.append(path)
        return age

    port = PortProbe(free=False)

    code, results = run_doctor(probes=fake_probes(port=port, file_age=file_age))

    assert statuses(results) == ALL_OK | {"port_free": status}
    assert code == (0 if status == "ok" else 1)
    assert port.calls == [("127.0.0.1", 8766)]
    assert asked == [healthy.parent / "heartbeat"]
    if status == "ok":
        assert "held by this environment's running engine" in results["port_free"][1]


def test_a_real_heartbeat_file_is_read(healthy: Path) -> None:
    (healthy.parent / "heartbeat").touch()
    probes = fake_probes(port=PortProbe(free=False), file_age=Probes.system().file_age)

    code, results = run_doctor(probes=probes)

    assert code == 0
    assert results["port_free"][0] == "ok"


def test_a_port_probe_that_raises_fails(healthy: Path) -> None:
    def broken(host: str, port: int) -> bool:
        raise OSError("planted detail")

    code, results = run_doctor(probes=fake_probes(port_is_free=broken))

    assert code == 1
    assert results["port_free"] == ("fail", "cannot probe 127.0.0.1:8766 (OSError)")


# Row: Backup dir --------------------------------------------------------------------------------


def _backup_dirs(data_dir: Path) -> dict[str, Path | str]:
    return {
        "equal-other-case": str(data_dir).upper(),
        "inside-the-data-dir": data_dir / "backups",
        "containing-it": data_dir.parent,
        "onedrive": data_dir.parent.parent / "OneDrive" / "Backups",
    }


@pytest.mark.parametrize(
    "case", ["equal-other-case", "inside-the-data-dir", "containing-it", "onedrive"]
)
def test_a_backup_dir_that_is_not_separate_fails(
    healthy: Path, case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = healthy.parent
    monkeypatch.setenv("NEXTRON_BACKUP_DIR", str(_backup_dirs(data_dir)[case]))
    environ = {"OneDrive": str(data_dir.parent.parent / "OneDrive")}

    code, results = run_doctor(probes=fake_probes(environ=environ))

    assert code == 1
    # the ledger is in the default backup dir, not in this one (Story 3.16)
    assert statuses(results) == ALL_OK | {"backup_dir_separate": "fail", "erasure_ledger": "fail"}
    assert not (data_dir / "backups").exists()


def test_the_default_backup_dir_is_separate(healthy: Path, local_app_data: Path) -> None:
    code, results = run_doctor()

    assert code == 0
    status, message = results["backup_dir_separate"]
    assert status == "ok"
    assert str(local_app_data / "Nextron" / "backups" / "dev") in message


# Row: Credential --------------------------------------------------------------------------------


class FailingReads(InMemorySecrets):
    """``InMemorySecrets`` whose reads of ``failing_names`` fail with ``keyring_failed``."""

    def __init__(self, failing_names: set[str]) -> None:
        super().__init__("dev")
        self.failing_names = failing_names

    def read(self, name: str) -> str | None:
        if name in self.failing_names:
            raise AdapterError(False, KEYRING_FAILED, f"read of secret {name} failed")
        return super().read(name)


def test_a_missing_credential_fails_naming_it(healthy: Path) -> None:
    secrets = healthy_secrets()
    secrets.delete("hunter/api_key")

    code, results = run_doctor(secrets=secrets)

    assert code == 1
    assert statuses(results) == ALL_OK | {"required_credentials": "fail"}
    assert results["required_credentials"][1] == (
        "required credentials not set: Hunter API key; enter them on the Credentials page"
    )


def test_a_credential_of_1281_units_fails_naming_it(healthy: Path) -> None:
    secrets = healthy_secrets()
    long_value = PLANTED_CREDENTIAL + "x" * (1281 - len(PLANTED_CREDENTIAL))
    secrets.store[(secrets.service, "telegram/bot_token")] = long_value  # past the port's check

    code, results = run_doctor(secrets=secrets)

    assert code == 1
    assert results["required_credentials"] == (
        "fail",
        "required credentials over 1,280 characters: Telegram bot token; enter them on the "
        "Credentials page",
    )


def test_an_unreadable_credential_fails_naming_it(healthy: Path) -> None:
    secrets = FailingReads({"anthropic/api_key"})
    source = healthy_secrets()
    secrets.store.update(source.store)

    code, results = run_doctor(secrets=secrets)

    assert code == 1
    assert statuses(results) == ALL_OK | {"required_credentials": "fail"}
    assert "unreadable (Windows Credential Manager failed): Claude API key" in (
        results["required_credentials"][1]
    )


# Row: Grant -------------------------------------------------------------------------------------


def test_an_invalid_grant_fails_and_marks_the_grant_for_reconsent(healthy: Path) -> None:
    secrets = healthy_secrets()
    oauth = FakeGoogleOAuth()
    oauth.refreshes.append(AdapterError(False, "google_invalid_grant", "invalid_grant"))

    code, results = run_doctor(secrets=secrets, oauth=oauth)

    assert code == 1
    assert statuses(results) == ALL_OK | {"internal_notify_grant": "fail"}
    assert "needs re-consent" in results["internal_notify_grant"][1]
    meta = decode_grant_meta(secrets.read(NOTIFY.meta_name))
    assert meta is not None and meta.needs_reconsent_at == FrozenClock().now()


def test_a_grant_already_marked_for_reconsent_fails_with_no_call(healthy: Path) -> None:
    secrets = healthy_secrets()
    marked = GrantMeta(PLANTED_ACCOUNT, GRANTED_AT, needs_reconsent_at=GRANTED_AT)
    secrets.write(NOTIFY.meta_name, encode_grant_meta(marked))
    oauth = FakeGoogleOAuth()

    code, results = run_doctor(secrets=secrets, oauth=oauth)

    assert code == 1
    assert results["internal_notify_grant"][0] == "fail"
    assert "needs re-consent" in results["internal_notify_grant"][1]
    assert oauth.calls == []


def test_google_unreachable_is_only_a_warning(healthy: Path) -> None:
    oauth = FakeGoogleOAuth()
    oauth.refreshes.append(AdapterError(True, "google_unavailable", "the request failed"))

    code, results = run_doctor(oauth=oauth)

    assert code == 0
    assert statuses(results) == ALL_OK | {"internal_notify_grant": "warn"}
    assert "google_unavailable" in results["internal_notify_grant"][1]


@pytest.mark.parametrize(
    ("missing", "says"),
    [
        (CLIENT_SECRET_NAME, "the Google Desktop client is not imported"),
        (NOTIFY.secret_name, "internal_notify is not granted"),
    ],
    ids=["no-client", "no-grant"],
)
def test_no_client_or_no_grant_fails_with_no_call(healthy: Path, missing: str, says: str) -> None:
    secrets = healthy_secrets()
    secrets.delete(missing)
    oauth = FakeGoogleOAuth()

    code, results = run_doctor(secrets=secrets, oauth=oauth)

    assert code == 1
    assert statuses(results) == ALL_OK | {"internal_notify_grant": "fail"}
    assert says in results["internal_notify_grant"][1]
    assert oauth.calls == []


def test_an_unreadable_credential_manager_fails_the_grant(healthy: Path) -> None:
    secrets = FailingReads({CLIENT_SECRET_NAME})
    secrets.store.update(healthy_secrets().store)

    code, results = run_doctor(secrets=secrets)

    assert results["internal_notify_grant"] == (
        "fail",
        "Windows Credential Manager could not be read (keyring_failed)",
    )
    assert code == 1


# Row: Off-PC backup (Story 1.17) ------------------------------------------------------------------


def test_an_off_pc_folder_on_the_data_dirs_drive_warns_and_is_not_touched(data_dir: Path) -> None:
    folder = data_dir.parent / "offpc"
    make_database(data_dir, {**SETTINGS, "backup_offpc_dir": str(folder)}, offpc=False)

    code, results = run_doctor()

    assert code == 0  # a warning is not a failure
    assert statuses(results) == ALL_OK | {"offpc_backup": "warn"}
    assert results["offpc_backup"][1].startswith(
        f"off-PC backup folder {folder} is on the same drive as the data dir"
    )
    assert not folder.exists()


def test_an_off_pc_folder_in_the_data_dir_is_not_configured(data_dir: Path) -> None:
    make_database(data_dir, {**SETTINGS, "backup_offpc_dir": str(data_dir)}, offpc=False)

    code, results = run_doctor()

    assert statuses(results) == ALL_OK | {"offpc_backup": "warn"}
    assert results["offpc_backup"][1].startswith(
        "off-PC backup not configured (OQ-A3): backups are local only; the off-PC backup "
        f"folder {data_dir} is in the data dir {data_dir}; "
    )


# Row: Settings ----------------------------------------------------------------------------------


def test_an_unset_required_setting_fails_naming_it(data_dir: Path) -> None:
    make_database(
        data_dir,
        {"sender_name": "Furqaan Ahmed", "sender_timezone": "Europe/London", **FOOTER_SETTINGS},
    )

    code, results = run_doctor()

    assert code == 1
    assert statuses(results) == ALL_OK | {"required_settings": "fail"}
    assert results["required_settings"][1] == (
        "required settings not set or no longer valid: Telegram alert chat id (alert_chat_id); "
        "set them on the Settings page"
    )


def test_a_stored_setting_that_no_longer_parses_counts_as_missing(healthy: Path) -> None:
    run_sql(healthy, "UPDATE settings SET value = 'Mars/Base' WHERE key = 'sender_timezone'")

    code, results = run_doctor()

    assert code == 1
    assert statuses(results) == ALL_OK | {"required_settings": "fail"}
    assert "Sender's timezone (sender_timezone)" in results["required_settings"][1]
    assert "Mars/Base" not in results["required_settings"][1]
    assert "alert_chat_id" not in results["required_settings"][1]


# Row: --env -------------------------------------------------------------------------------------


def test_env_overrides_nextron_env_for_the_run(
    local_app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    namespaces: list[str] = []

    def keyring(env: str) -> InMemorySecrets:
        namespaces.append(env)
        return InMemorySecrets(env)

    monkeypatch.setattr(main, "KeyringSecrets", keyring)
    asked: list[PurePath] = []

    def file_age(path: PurePath) -> float | None:
        asked.append(path)
        return None

    port = PortProbe(free=False)
    out = io.StringIO()

    code = main.doctor(
        env="prod",
        as_json=True,
        probes=fake_probes(port=port, file_age=file_age),
        google_oauth=FakeGoogleOAuth(),
        clock=FrozenClock(),
        out=out,
        err=io.StringIO(),
    )

    assert code == 1
    results = {row["id"]: row["message"] for row in json.loads(out.getvalue())}
    prod = local_app_data / "Nextron" / "prod"
    assert namespaces == ["prod"]
    assert port.calls == [("127.0.0.1", 8765)]
    assert asked == [prod / "heartbeat"]
    assert str(prod) in results["data_dir_outside_onedrive"]
    assert results["prod_env_not_persistent"].endswith("; this run is prod, from --env")
    assert not prod.exists()


def test_without_env_the_message_names_nextron_env(healthy: Path) -> None:
    code, results = run_doctor()

    assert code == 0
    assert results["prod_env_not_persistent"][1].endswith("; this run is dev, from NEXTRON_ENV")


def test_the_cli_refuses_an_invalid_env(capsys: pytest.CaptureFixture[str]) -> None:
    from nextron import __main__ as cli

    with pytest.raises(SystemExit) as exited:
        cli.main(["doctor", "--env", "staging"])

    assert exited.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


# Row: JSON --------------------------------------------------------------------------------------


def test_json_holds_the_same_list_as_the_table(healthy: Path) -> None:
    secrets = healthy_secrets()
    secrets.delete("hunter/api_key")

    table_code, table, _ = doctor_output(secrets=secrets)
    json_code, text, _ = doctor_output(as_json=True, secrets=secrets)

    assert table_code == json_code == 1
    rows = json.loads(text)
    assert all(set(row) == {"id", "status", "message"} for row in rows)
    assert [(row["status"], row["id"], row["message"]) for row in rows] == table_rows(table)
    assert text.isascii()


# Row: Crash -------------------------------------------------------------------------------------


def test_a_crashed_check_fails_and_the_rest_still_run(
    healthy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(probes: Probes) -> Any:
        raise RuntimeError("planted detail")

    monkeypatch.setattr(main, "check_tzdata", boom)

    code, results = run_doctor()

    assert code == 1
    assert statuses(results) == ALL_OK | {"tzdata": "fail"}
    assert results["tzdata"][1] == "the check crashed (RuntimeError)"


# Row: Secrets -----------------------------------------------------------------------------------


@pytest.mark.parametrize("as_json", [False, True], ids=["table", "json"])
@pytest.mark.parametrize(
    "refresh",
    [
        None,
        AdapterError(False, "google_rejected", f"rejected {PLANTED_REFRESH} {PLANTED_ACCOUNT}"),
        AdapterError(False, "google_invalid_grant", f"invalid_grant {PLANTED_REFRESH}"),
    ],
    ids=["refreshed", "rejected", "invalid-grant"],
)
def test_no_secret_or_account_reaches_the_output(
    healthy: Path,
    as_json: bool,
    refresh: AdapterError | None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets = healthy_secrets()
    secrets.store[(secrets.service, "telegram/bot_token")] = PLANTED_CREDENTIAL * 70  # too long
    oauth = FakeGoogleOAuth()
    if refresh is not None:
        oauth.refreshes.append(refresh)

    code, out, err = doctor_output(as_json=as_json, secrets=secrets, oauth=oauth)

    assert code == 1
    captured = capsys.readouterr()
    for text in (out, err, captured.out, captured.err):
        for planted in PLANTED:
            assert planted not in text
        assert "@" not in text
    assert oauth.calls == [("refresh", PLANTED_REFRESH)]


# Kept from Story 1.8: a persistent NEXTRON_ENV -------------------------------------------------


def _raising(name: str) -> dict[str, str]:
    raise PermissionError("registry access denied")


@pytest.mark.parametrize(
    ("persistent", "status", "code"),
    [
        (lambda name: {"user": "prod"}, "fail", 1),
        (lambda name: {"machine": "PROD "}, "fail", 1),
        (lambda name: {"user": "dev"}, "ok", 0),
        (lambda name: {}, "ok", 0),
        (_raising, "fail", 1),
    ],
    ids=["user-prod", "machine-PROD", "only-dev", "none", "probe-raises"],
)
def test_a_persistent_prod_env_fails_doctor(
    persistent: Callable[[str], dict[str, str]], status: str, code: int, healthy: Path
) -> None:
    exit_code, results = run_doctor(probes=fake_probes(persistent_variable=persistent))

    assert exit_code == code
    assert statuses(results) == ALL_OK | {"prod_env_not_persistent": status}
    message = results["prod_env_not_persistent"][1]
    if status == "fail" and persistent is not _raising:
        assert message.startswith("NEXTRON_ENV=prod must only be set by the logon task")
    if persistent is _raising:
        assert "PermissionError" in message and "access denied" not in message


def test_doctor_asks_only_for_nextron_env(healthy: Path) -> None:
    asked: list[str] = []

    def persistent(name: str) -> dict[str, str]:
        asked.append(name)
        return {}

    assert run_doctor(probes=fake_probes(persistent_variable=persistent))[0] == 0
    assert asked == ["NEXTRON_ENV"]


def test_serve_does_not_check_the_persistent_env(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refusing a persistent value at ``serve`` would also stop the logon task's own ``prod``
    start, which inherits it."""
    started: list[bool] = []

    def persistent(name: str) -> dict[str, str]:
        raise AssertionError("serve must not read the persistent environment")

    code = main.serve(
        probes=fake_probes(persistent_variable=persistent),
        run_server=lambda config, reload, units: started.append(True),
        err=io.StringIO(),
    )

    assert code == 0 and started == [True]


def test_doctor_takes_no_lock(healthy: Path) -> None:
    with InstanceLock(healthy.parent / "nextron.lock"):  # a running engine holds it
        code, results = run_doctor()

    assert code == 0
    assert statuses(results) == ALL_OK


def test_doctor_with_env_unset_exits_2(
    local_app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEXTRON_ENV")

    code, out, err = doctor_output()

    assert code == 2
    assert out == ""
    assert err.strip() == ENV_ERROR
    assert list(local_app_data.iterdir()) == []


def test_cli_dispatches_doctor_with_its_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    from nextron import __main__ as cli

    calls: list[dict[str, object]] = []

    def doctor(**kwargs: object) -> int:
        calls.append(kwargs)
        return 1

    monkeypatch.setattr(cli.engine, "doctor", doctor)

    assert cli.main(["doctor"]) == 1
    assert cli.main(["doctor", "--env", "prod", "--json"]) == 1
    assert calls == [{"env": None, "as_json": False}, {"env": "prod", "as_json": True}]


# DoctorDatabase ---------------------------------------------------------------------------------


def test_the_doctor_database_never_creates_a_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "data" / "nextron.db"
    database = main.DoctorDatabase(path)

    assert not database.exists()
    assert database.required_unset() == (
        "sender_name",
        "sender_timezone",
        "alert_chat_id",
        *FOOTER_UNSET,
    )
    assert not path.parent.exists()  # no engine was needed
    path.parent.mkdir()
    with pytest.raises(OperationalError, match="unable to open database file"):
        database.revision()
    database.close()
    database.close()  # twice is fine

    assert list(path.parent.iterdir()) == []


def test_the_doctor_database_reads_revision_and_settings(healthy: Path) -> None:
    database = main.DoctorDatabase(healthy)
    try:
        assert database.exists()
        assert database.revision() == head_revision()
        assert database.required_unset() == ()
    finally:
        database.close()


# Row: the unsubscribe Worker (Story 3.12) -------------------------------------------------------

WORKER_HOST = "u.example-sender.com"


def worker_database(data_dir: Path) -> Path:
    return make_database(data_dir, {**SETTINGS, "unsub_worker_host": WORKER_HOST})


def test_a_reachable_worker_is_ok_through_the_injected_port(data_dir: Path) -> None:
    worker_database(data_dir)
    port = ScriptedUnsubscribe()
    out, err = io.StringIO(), io.StringIO()

    code = main.doctor(
        as_json=True,
        probes=fake_probes(),
        secrets=healthy_secrets(),
        google_oauth=FakeGoogleOAuth(),
        clock=FrozenClock(),
        unsubscribe=port,
        out=out,
        err=err,
    )

    results = {row["id"]: (row["status"], row["message"]) for row in json.loads(out.getvalue())}
    assert (code, err.getvalue()) == (0, "")
    assert results["unsub_worker"] == ("ok", "the unsubscribe Worker is reachable")
    assert port.probes == [(WORKER_HOST, 10.0)]


def test_a_worker_answering_after_11_seconds_warns(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_database(data_dir)
    meters: list[Any] = []
    real = main.WorkerUnsubscribe
    reads = iter([100.0, 111.0])

    def worker(secrets: Any, *, meter: Any) -> Any:
        meters.append(meter)
        transport = httpx2.MockTransport(
            lambda request: httpx2.Response(
                200, json={"format": "nextron-unsub-pending/1", "entries": []}
            )
        )
        return real(
            secrets,
            meter=meter,
            http=httpx2.Client(transport=transport),
            monotonic=lambda: next(reads),
        )

    monkeypatch.setattr(main, "WorkerUnsubscribe", worker)
    secrets = healthy_secrets()
    secrets.write("unsub/bearer", "Ub4Xq8Lm2Vz6Np1Rt5Hk9Jc3Wd7Gs0Fy4Tb8Mn2Qe6Ah")

    code, results = run_doctor(secrets=secrets)

    assert code == 0  # a warning doesn't fail doctor
    assert results["unsub_worker"] == (
        "warn",
        "the unsubscribe Worker was slower than 10 s (unsub_unreachable); polling retries "
        "every period",
    )
    [meter] = meters
    assert isinstance(meter, main.DoctorMeter)  # the probe writes no usage row
    assert meter.calls == [
        ApiCall("unsub_worker", "kv_list", ok=False, error_code="unsub_unreachable")
    ]


def test_polling_on_without_a_bearer_fails(data_dir: Path) -> None:
    worker_database(data_dir)
    secrets = healthy_secrets()
    secrets.delete("unsub/bearer")

    code, results = run_doctor(secrets=secrets)

    assert code == 1
    assert results["unsub_worker"][0] == "fail"
    assert "polling on but bearer not set" in results["unsub_worker"][1]
