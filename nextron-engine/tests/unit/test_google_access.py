"""``GoogleAccess`` (``nextron.app.setup.api``) with fakes: every row of the story's matrix at
service level (import, grant start, timeout, bad state, declined, granted, wrong domain, not
allowlisted, bad tokens, re-grant, remove, refresh and needs re-consent), plus the waiting state
before the job runs, a superseded flow, the lock order, and the access token never reaching the
keyring."""

import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest

import nextron.app.setup.api as api
from nextron.app.setup.api import (
    ClientImport,
    ClientStatus,
    ConsentOutcome,
    CredentialState,
    CredentialStoreFailed,
    GoogleAccess,
    GrantState,
    Revocation,
)
from nextron.domain import google_oauth as g
from nextron.domain.google_oauth import (
    ClientConfig,
    ClientNotImported,
    ConsentFailure,
    GrantMeta,
    InvalidClientFile,
    UnknownGrant,
    code_challenge,
    decode_grant_meta,
    encode_client_config,
    encode_grant_meta,
)
from nextron.ports.errors import AdapterError
from nextron.ports.google_oauth import (
    CLIENT_FILE_NOT_DELETED,
    CLIENT_FILE_NOT_REGULAR,
    CLIENT_FILE_TOO_LARGE,
    CLIENT_FILE_UNREADABLE,
    Query,
)
from nextron.ports.secrets import SECRET_TOO_LONG
from tests.dashboard import RecordingBrowser
from tests.fakes.clock import FrozenClock
from tests.fakes.google_oauth import (
    CLIENT_ID,
    FakeClientFiles,
    FakeConsentListener,
    FakeGoogleOAuth,
    id_token,
    token_set,
)
from tests.fakes.secrets import InMemorySecrets

NOW = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
SERVICE = "nextron-engine/test"
SECRET = "Cs4Vq8Lp2Xz6Nw1Rt5Hy"
STATE = "St3Lm8Qw2Zn6Vb9Hx4Ck" * 2
VERIFIER = "Vf2Bn6Qr9Lk3Hs7Wd1Zm" * 4
CODE = "Ac7Wm3Kq9Jz2Pv5Tx8Ns"
PATH = r"C:\Users\mac\Downloads\client_secret_1.json"
MAC = "mac@nextronai.com"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send openid email"
POSTMASTER_SCOPE = "https://www.googleapis.com/auth/postmaster.readonly openid email"
CLIENT = ClientConfig(CLIENT_ID, SECRET)
CLIENT_FILE = (
    '{"installed": {"client_id": "%s", "client_secret": "%s", "project_id": "nextron"}}'
    % (CLIENT_ID, SECRET)
).encode()
ANSWER: Query = {"state": (STATE,), "code": (CODE,), "scope": (GMAIL_SCOPE,)}


@pytest.fixture(autouse=True)
def fixed_randomness(monkeypatch: pytest.MonkeyPatch) -> None:
    """``state`` and ``verifier`` are known, so the tests can answer as Google would."""
    monkeypatch.setattr(api, "token_urlsafe", lambda size: {32: STATE, 64: VERIFIER}[size])


@dataclass
class World:
    """A ``GoogleAccess`` on fakes; ``jobs`` holds each flow's job until a test runs it."""

    env: str = "dev"
    allowlist: list[str] = field(default_factory=lambda: [MAC, "x@gmail.com", "b@nextronai.com"])
    allowlist_error: Exception | None = None
    secrets: InMemorySecrets = field(default_factory=lambda: InMemorySecrets("test"))
    clock: FrozenClock = field(default_factory=lambda: FrozenClock(NOW))
    oauth: FakeGoogleOAuth = field(default_factory=FakeGoogleOAuth)
    files: FakeClientFiles = field(default_factory=FakeClientFiles)
    browser: RecordingBrowser = field(default_factory=RecordingBrowser)
    next_answer: Query | None = None
    listen_error: Exception | None = None
    listeners: list[FakeConsentListener] = field(default_factory=list)
    jobs: list[Any] = field(default_factory=list)
    allowlist_reads: int = 0
    access: GoogleAccess = field(init=False)

    def __post_init__(self) -> None:
        self.access = GoogleAccess(
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

    def listen(self) -> FakeConsentListener:
        if self.listen_error is not None:
            raise self.listen_error
        self.listeners.append(FakeConsentListener(self.next_answer))
        return self.listeners[-1]

    def read_allowlist(self) -> list[str]:
        self.allowlist_reads += 1
        if self.allowlist_error is not None:
            raise self.allowlist_error
        return list(self.allowlist)

    def stored(self) -> dict[str, str]:
        return {n: v for (s, n), v in self.secrets.store.items() if s == SERVICE}

    def put(self, name: str, value: str) -> None:
        self.secrets.store[(SERVICE, name)] = value

    def import_client(self) -> None:
        self.put(g.CLIENT_SECRET_NAME, encode_client_config(CLIENT))

    def put_grant(self, grant: str, token: str, account: str | None) -> None:
        spec = g.grant_spec(grant)
        self.put(spec.secret_name, token)
        self.put(spec.meta_name, encode_grant_meta(GrantMeta(account, NOW - timedelta(days=1))))

    def run_jobs(self) -> None:
        jobs, self.jobs[:] = list(self.jobs), []
        for job in jobs:
            job()

    def changes(self) -> list[tuple[str, str]]:
        return [call for call in self.secrets.calls if call[0] in ("write", "delete")]

    def grant(
        self,
        grant: str = "internal_notify",
        tokens: Any = None,
        answer: Query | None = ANSWER,
    ) -> api.GrantStatus:
        """Click Grant, have Google answer ``answer`` (exchanged for ``tokens``) and complete."""
        if tokens is not None:
            self.oauth.exchanges.append(tokens)
        self.next_answer = answer
        self.access.start_grant(grant)
        self.run_jobs()
        return self.access.grant_status(grant)


@pytest.fixture
def world() -> World:
    return World()


def outcome_of(world: World, grant: str = "internal_notify") -> ConsentOutcome | None:
    return world.access.grant_status(grant).outcome


def google_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith("google_")]


# Row: Import ----------------------------------------------------------------------------------


def test_importing_stores_only_the_id_and_secret_then_deletes_the_file(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    world.files.files[PATH] = CLIENT_FILE

    result = world.access.import_client(f'"{PATH}"')

    assert result == ClientImport(ClientStatus(CredentialState.SET, NOW), file_deleted=True)
    assert world.stored() == {
        "google/client_config": f'{{"client_id":"{CLIENT_ID}","client_secret":"{SECRET}"}}',
        "meta/google/client_config": "2026-11-17T09:30:00.000000Z",
    }
    assert world.files.reads == [(PATH, 65_536)]
    assert world.files.deleted == [PATH]
    assert world.changes() == [
        ("write", "google/client_config"),
        ("write", "meta/google/client_config"),
    ]
    assert [r.getMessage() for r in google_records(caplog)] == ["google_client_imported"]
    assert world.access.client_status() == ClientStatus(CredentialState.SET, NOW)


@pytest.mark.parametrize(
    ("typed", "content", "read_error", "reason"),
    [
        (PATH, b'{"web": {"client_id": "x"}}', None, g.WEB_CLIENT),
        (PATH, b"not json", None, g.NOT_JSON),
        (PATH, CLIENT_FILE.replace(b".apps.", b".app."), None, g.BAD_CLIENT_ID),
        ("client_secret.json", CLIENT_FILE, None, g.PATH_NOT_LOCAL),
        (r"\\srv\x.json", CLIENT_FILE, None, g.PATH_NOT_LOCAL),
        (r"C:\x\client_secret.txt", CLIENT_FILE, None, g.PATH_NOT_JSON),
        (r"C:\x\missing.json", None, None, g.FILE_MISSING),
        (PATH, CLIENT_FILE, CLIENT_FILE_NOT_REGULAR, g.FILE_NOT_REGULAR),
        (PATH, CLIENT_FILE, CLIENT_FILE_TOO_LARGE, g.FILE_TOO_LARGE),
        (PATH, CLIENT_FILE, CLIENT_FILE_UNREADABLE, g.FILE_UNREADABLE),
    ],
)
def test_a_refused_import_stores_nothing_and_leaves_the_file(
    typed: str, content: bytes | None, read_error: str | None, reason: str, world: World
) -> None:
    if content is not None:
        world.files.files[PATH] = content
    world.files.read_error = read_error

    with pytest.raises(InvalidClientFile) as caught:
        world.access.import_client(typed)

    assert caught.value.reason == reason
    assert world.stored() == {}
    assert world.files.deleted == []
    assert world.access.client_status() == ClientStatus(CredentialState.NOT_SET, None)
    assert SECRET not in str(caught.value) and typed not in str(caught.value)


def test_an_encoded_client_over_1280_units_is_refused(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.files.files[PATH] = CLIENT_FILE
    monkeypatch.setattr(api, "encode_client_config", lambda config: "x" * 1281)

    with pytest.raises(InvalidClientFile) as caught:
        world.access.import_client(PATH)

    assert caught.value.reason == SECRET_TOO_LONG
    assert world.stored() == {} and world.files.deleted == []


def test_an_import_whose_keyring_write_fails_leaves_the_file(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    world.files.files[PATH] = CLIENT_FILE
    world.secrets.failing = {"write"}

    with pytest.raises(CredentialStoreFailed) as caught:
        world.access.import_client(PATH)

    assert caught.value.key == "google_client"
    assert world.files.deleted == [] and PATH in world.files.files
    [record] = google_records(caplog)
    assert (record.getMessage(), record.code) == ("google_store_failed", "keyring_failed")  # type: ignore[attr-defined]


def test_an_import_whose_file_delete_fails_says_so(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    world.files.files[PATH] = CLIENT_FILE
    world.files.delete_error = CLIENT_FILE_NOT_DELETED

    result = world.access.import_client(PATH)

    assert result.file_deleted is False
    assert world.stored()["google/client_config"].startswith('{"client_id"')
    assert [(r.getMessage(), getattr(r, "code", None)) for r in google_records(caplog)] == [
        ("google_client_imported", None),
        ("google_client_file_kept", "client_file_not_deleted"),
    ]


def test_an_import_drops_the_cached_access_tokens(world: World) -> None:
    world.import_client()
    world.grant()
    world.files.files[PATH] = CLIENT_FILE

    world.access.import_client(PATH)
    world.oauth.refreshes.append(token_set("ya29.after-import"))

    assert world.access.access_token("internal_notify") == "ya29.after-import"


def test_a_failing_keyring_read_shows_the_client_as_unknown(world: World) -> None:
    world.secrets.failing = {"read"}

    assert world.access.client_status() == ClientStatus(CredentialState.UNKNOWN, None)


# Row: Grant start -------------------------------------------------------------------------------


def test_grant_opens_a_listener_and_the_consent_page_and_stores_nothing(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    world.import_client()
    world.secrets.calls.clear()

    status = world.access.start_grant("internal_notify")

    [listener] = world.listeners
    [url] = world.browser.urls
    query = parse_qsl(urlsplit(url).query, strict_parsing=True)
    assert url.startswith(g.AUTH_URI + "?")
    assert query == [
        ("response_type", "code"),
        ("client_id", CLIENT_ID),
        ("redirect_uri", listener.redirect_uri()),
        ("scope", GMAIL_SCOPE),
        ("state", STATE),
        ("code_challenge", code_challenge(VERIFIER)),
        ("code_challenge_method", "S256"),
        ("access_type", "offline"),
        ("prompt", "select_account consent"),
    ]
    assert status.waiting_until == NOW + timedelta(minutes=5)
    assert status.state is GrantState.NOT_GRANTED and status.outcome is None
    assert world.changes() == []
    assert len(world.jobs) == 1 and listener.waited == [] and not listener.closed
    assert world.oauth.calls == []
    assert [r.getMessage() for r in google_records(caplog)] == ["google_consent_started"]


def test_the_flow_waits_until_its_job_runs(world: World) -> None:
    world.import_client()
    world.access.start_grant("postmaster")

    assert world.access.grant_status("postmaster").waiting_until == NOW + timedelta(minutes=5)
    assert world.access.grant_status("internal_notify").waiting_until is None

    world.oauth.exchanges.append(token_set(scope=POSTMASTER_SCOPE))
    world.listeners[-1].answer = ANSWER
    world.run_jobs()

    status = world.access.grant_status("postmaster")
    assert status.waiting_until is None
    assert status.state is GrantState.GRANTED
    assert world.listeners[-1].waited == [300.0]
    assert world.listeners[-1].closed


@pytest.mark.parametrize("stored", [None, "not a client", '{"client_id": "x"}'])
def test_grant_without_an_imported_client_starts_nothing(
    stored: str | None, world: World
) -> None:
    if stored is not None:
        world.put(g.CLIENT_SECRET_NAME, stored)

    with pytest.raises(ClientNotImported):
        world.access.start_grant("internal_notify")

    assert world.listeners == [] and world.browser.urls == [] and world.jobs == []


def test_grant_with_an_unreadable_keyring_is_a_store_failure(world: World) -> None:
    world.secrets.failing = {"read"}

    with pytest.raises(CredentialStoreFailed):
        world.access.start_grant("internal_notify")

    assert world.listeners == [] and world.browser.urls == []


def test_an_unknown_grant_is_refused(world: World) -> None:
    world.import_client()

    for call in (
        world.access.start_grant,
        world.access.grant_status,
        world.access.remove_grant,
        world.access.access_token,
    ):
        with pytest.raises(UnknownGrant):
            call("gmail")
    assert world.listeners == []


@pytest.mark.parametrize("failure", ["false", "raises"])
def test_a_browser_that_does_not_open_ends_the_flow(failure: str, world: World) -> None:
    world.import_client()
    if failure == "false":
        world.browser.opens = False
    else:
        world.browser.error = OSError("no browser")

    status = world.access.start_grant("internal_notify")

    assert status.waiting_until is None
    assert status.outcome == ConsentOutcome(ConsentFailure.BROWSER_FAILED)
    assert world.listeners[0].closed and world.jobs == []


def test_a_listener_that_cannot_start_ends_the_flow(world: World) -> None:
    world.import_client()
    world.listen_error = AdapterError(False, "listener_failed", "the consent listener failed")

    status = world.access.start_grant("internal_notify")

    assert status.outcome == ConsentOutcome(ConsentFailure.LISTENER_FAILED, code="listener_failed")
    assert world.browser.urls == [] and world.jobs == []


def test_a_job_that_cannot_start_ends_the_flow(world: World) -> None:
    world.import_client()

    def refuse(job: object) -> None:
        raise RuntimeError("can't start new thread")

    world.access._start = refuse  # type: ignore[method-assign]
    status = world.access.start_grant("internal_notify")

    assert status.outcome == ConsentOutcome(ConsentFailure.CRASHED)
    assert world.listeners[0].closed


# Rows: Timeout, Bad state, Declined -------------------------------------------------------------


def test_no_answer_within_5_minutes_is_timed_out_and_nothing_is_exchanged(world: World) -> None:
    world.import_client()

    status = world.grant(answer=None)

    assert status.outcome == ConsentOutcome(ConsentFailure.TIMED_OUT)
    assert world.oauth.calls == [] and world.changes() == []
    assert world.listeners[0].waited == [300.0] and world.listeners[0].closed


@pytest.mark.parametrize(
    "answer",
    [
        {"code": (CODE,)},
        {"state": ("wrong",), "code": (CODE,)},
        {"state": (STATE, STATE), "code": (CODE,)},
        {"state": (STATE[:-1],), "code": (CODE,)},
        {"state": (STATE + "\u00e9",), "code": (CODE,)},
        {"state": ("",), "code": (CODE,)},
        {},
    ],
    ids=["missing", "wrong", "repeated", "prefix", "non_ascii", "blank", "empty"],
)
def test_a_wrong_state_ends_the_flow_without_an_exchange(answer: Query, world: World) -> None:
    world.import_client()

    status = world.grant(answer=answer)

    assert status.outcome == ConsentOutcome(ConsentFailure.STATE_MISMATCH)
    assert world.oauth.calls == [] and world.changes() == []
    assert status.state is GrantState.NOT_GRANTED


@pytest.mark.parametrize(
    "answer",
    [
        {"state": (STATE,), "error": ("access_denied",)},
        {"state": (STATE,), "error": ("access_denied",), "code": (CODE,)},
        {"state": (STATE,)},
        {"state": (STATE,), "code": (CODE, CODE)},
        {"state": (STATE,), "code": ("",)},
    ],
    ids=["denied", "error_and_code", "no_code", "two_codes", "blank_code"],
)
def test_a_declined_consent_ends_the_flow(answer: Query, world: World) -> None:
    world.import_client()

    status = world.grant(answer=answer)

    assert status.outcome == ConsentOutcome(ConsentFailure.DECLINED)
    assert world.oauth.calls == [] and world.changes() == []


def test_a_failed_exchange_ends_the_flow_and_revokes_nothing(world: World) -> None:
    world.import_client()

    status = world.grant(tokens=AdapterError(False, "google_invalid_grant", "refused"))

    assert status.outcome == ConsentOutcome(
        ConsentFailure.EXCHANGE_FAILED, code="google_invalid_grant"
    )
    assert world.oauth.calls == [("exchange", CODE)] and world.changes() == []


# Row: Granted -----------------------------------------------------------------------------------


def test_a_matching_answer_stores_the_refresh_token_and_the_account(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    world.import_client()
    world.secrets.calls.clear()

    status = world.grant(tokens=token_set("ya29.first", refresh_token="1//first"))

    assert world.oauth.calls == [("exchange", CODE)]
    assert world.oauth.verifiers == [VERIFIER]
    assert world.oauth.redirect_uris == [world.listeners[0].redirect_uri()]
    assert world.oauth.clients == [CLIENT]
    assert world.stored()["google/internal_notify"] == "1//first"
    assert world.stored()["meta/google/internal_notify"] == (
        '{"account":"mac@nextronai.com","granted_at":"2026-11-17T09:30:00.000000Z"}'
    )
    assert world.changes() == [
        ("write", "google/internal_notify"),
        ("write", "meta/google/internal_notify"),
    ]
    assert (status.state, status.account, status.granted_at) == (GrantState.GRANTED, MAC, NOW)
    assert status.outcome == ConsentOutcome(None)
    assert world.listeners[0].closed
    assert world.access.access_token("internal_notify") == "ya29.first"
    assert world.oauth.calls == [("exchange", CODE)]  # the cached token: no refresh
    assert "ya29.first" not in world.stored().values()
    assert [r.getMessage() for r in google_records(caplog)] == [
        "google_consent_started",
        "google_grant_stored",
    ]


def test_postmaster_may_use_any_allowlisted_account(world: World) -> None:
    world.import_client()

    status = world.grant(
        "postmaster", token_set(scope=POSTMASTER_SCOPE, email="x@gmail.com")
    )

    assert (status.state, status.account) == (GrantState.GRANTED, "x@gmail.com")


# Rows: Wrong domain, Not allowlisted, Bad tokens -----------------------------------------------


def test_internal_notify_on_another_domain_is_revoked_and_refused(world: World) -> None:
    world.import_client()

    status = world.grant(tokens=token_set(email="x@gmail.com", refresh_token="1//gmail"))

    assert status.outcome == ConsentOutcome(ConsentFailure.NOT_INTERNAL_DOMAIN, Revocation.REVOKED)
    assert world.oauth.revoked == ["1//gmail"]
    assert world.changes() == []
    assert status.state is GrantState.NOT_GRANTED


@pytest.mark.parametrize("env", ["dev", "test"])
def test_outside_prod_an_account_not_on_the_allowlist_is_revoked_and_refused(env: str) -> None:
    world = World(env=env, allowlist=["someone@else.com"])
    world.import_client()

    status = world.grant(tokens=token_set(refresh_token="1//new"))

    assert status.outcome == ConsentOutcome(ConsentFailure.NOT_ALLOWLISTED, Revocation.REVOKED)
    assert world.oauth.revoked == ["1//new"] and world.changes() == []


def test_in_prod_the_allowlist_is_never_read() -> None:
    world = World(env="prod", allowlist=[], allowlist_error=RuntimeError("no database"))
    world.import_client()

    status = world.grant()

    assert status.state is GrantState.GRANTED
    assert world.allowlist_reads == 0


def test_an_unreadable_allowlist_counts_as_empty(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    world = World(allowlist_error=RuntimeError("database is locked"))
    world.import_client()

    status = world.grant()

    assert status.outcome == ConsentOutcome(ConsentFailure.NOT_ALLOWLISTED, Revocation.REVOKED)
    assert "google_allowlist_unreadable" in [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize(
    ("tokens", "failure", "revoked"),
    [
        (
            token_set(scope="openid email", refresh_token="1//r"),
            ConsentFailure.SCOPE_NOT_GRANTED,
            "1//r",
        ),
        (token_set("ya29.only", refresh_token=None), ConsentFailure.NO_REFRESH_TOKEN, "ya29.only"),
        (
            token_set(refresh_token="1//r", id_token_text=id_token(aud="other")),
            ConsentFailure.NO_ACCOUNT,
            "1//r",
        ),
        (
            token_set(refresh_token="1//r", id_token_text=id_token(exp=int(NOW.timestamp()))),
            ConsentFailure.NO_ACCOUNT,
            "1//r",
        ),
        (
            token_set(refresh_token="1//r", id_token_text=id_token(email_verified=False)),
            ConsentFailure.NO_ACCOUNT,
            "1//r",
        ),
    ],
    ids=["scope_missing", "no_refresh_token", "wrong_aud", "expired", "unverified"],
)
def test_bad_tokens_are_revoked_and_refused(
    tokens: Any, failure: ConsentFailure, revoked: str, world: World
) -> None:
    world.import_client()

    status = world.grant(tokens=tokens)

    assert status.outcome == ConsentOutcome(failure, Revocation.REVOKED)
    assert world.oauth.revoked == [revoked]
    assert world.changes() == []


def test_a_refused_token_on_an_account_another_grant_holds_is_not_revoked(world: World) -> None:
    world.import_client()
    world.put_grant("postmaster", "1//postmaster", "x@gmail.com")

    status = world.grant(tokens=token_set(email="x@gmail.com", refresh_token="1//new"))

    assert status.outcome == ConsentOutcome(ConsentFailure.NOT_INTERNAL_DOMAIN, Revocation.SHARED)
    assert world.oauth.revoked == []


def test_a_refusal_whose_revoke_fails_says_so(world: World) -> None:
    world.import_client()
    world.oauth.revoke_error = AdapterError(True, "google_unavailable", "down")

    status = world.grant(tokens=token_set(email="x@gmail.com"))

    assert status.outcome == ConsentOutcome(ConsentFailure.NOT_INTERNAL_DOMAIN, Revocation.FAILED)


# Row: Re-grant ----------------------------------------------------------------------------------


def test_regranting_another_account_stores_it_and_revokes_the_old_token(world: World) -> None:
    world.import_client()
    world.put_grant("internal_notify", "1//old-a", "a@nextronai.com")

    status = world.grant(tokens=token_set(email="b@nextronai.com", refresh_token="1//new-b"))

    assert (status.state, status.account) == (GrantState.GRANTED, "b@nextronai.com")
    assert world.stored()["google/internal_notify"] == "1//new-b"
    assert world.oauth.revoked == ["1//old-a"]
    assert status.outcome == ConsentOutcome(None, Revocation.REVOKED)


def test_regranting_the_same_account_does_not_revoke_the_old_token(world: World) -> None:
    world.import_client()
    world.put_grant("internal_notify", "1//old", "Mac@NextronAI.com")

    status = world.grant(tokens=token_set(email=MAC, refresh_token="1//new"))

    assert world.stored()["google/internal_notify"] == "1//new"
    assert world.oauth.revoked == []  # Google would end the new grant too
    assert status.outcome == ConsentOutcome(None, Revocation.SHARED)


def test_regranting_when_the_revoke_fails_still_stores(world: World) -> None:
    world.import_client()
    world.put_grant("internal_notify", "1//old-a", "a@nextronai.com")
    world.oauth.revoke_error = AdapterError(True, "google_unavailable", "down")

    status = world.grant(tokens=token_set(email="b@nextronai.com", refresh_token="1//new-b"))

    assert world.stored()["google/internal_notify"] == "1//new-b"
    assert status.outcome == ConsentOutcome(None, Revocation.FAILED)


def test_a_fresh_grant_clears_needs_reconsent(world: World) -> None:
    world.import_client()
    world.put(g.grant_spec("internal_notify").secret_name, "1//old")
    world.put(
        "meta/google/internal_notify", encode_grant_meta(GrantMeta(MAC, NOW, NOW))
    )
    assert world.access.grant_status("internal_notify").state is GrantState.NEEDS_RECONSENT

    status = world.grant(tokens=token_set(refresh_token="1//new"))

    assert status.state is GrantState.GRANTED and status.needs_reconsent_at is None


def test_a_store_failure_revokes_the_new_token_and_puts_the_old_one_back(world: World) -> None:
    world.import_client()
    world.put_grant("internal_notify", "1//old-a", "a@nextronai.com")
    before = dict(world.stored())
    real_write = world.secrets.write

    def failing_meta_write(name: str, value: str) -> None:
        if name == "meta/google/internal_notify" and "b@nextronai.com" in value:
            raise AdapterError(False, "keyring_failed", "write failed")
        real_write(name, value)

    world.secrets.write = failing_meta_write  # type: ignore[method-assign]

    status = world.grant(tokens=token_set(email="b@nextronai.com", refresh_token="1//new-b"))

    assert status.outcome == ConsentOutcome(
        ConsentFailure.STORE_FAILED, Revocation.REVOKED, "keyring_failed"
    )
    assert world.oauth.revoked == ["1//new-b"]
    assert world.stored() == before
    assert status.account == "a@nextronai.com"


def test_a_first_grant_whose_meta_write_fails_leaves_no_token(world: World) -> None:
    world.import_client()
    real_write = world.secrets.write

    def failing_meta_write(name: str, value: str) -> None:
        if name == "meta/google/internal_notify":
            raise AdapterError(False, "keyring_failed", "write failed")
        real_write(name, value)

    world.secrets.write = failing_meta_write  # type: ignore[method-assign]

    status = world.grant(tokens=token_set(refresh_token="1//first"))

    assert "google/internal_notify" not in world.stored()
    assert status.state is GrantState.NOT_GRANTED
    assert status.outcome == ConsentOutcome(
        ConsentFailure.STORE_FAILED, Revocation.REVOKED, "keyring_failed"
    )
    assert world.oauth.revoked == ["1//first"]


# A superseded flow ------------------------------------------------------------------------------


def test_a_flow_registered_by_a_concurrent_click_is_cancelled_when_overwritten(
    world: World,
) -> None:
    """A second Grant click that registers its flow between the first click's cancel and its
    registration (here: from inside the first click's ``listen``) is cancelled, not leaked."""
    world.import_client()
    real_listen = world.listen
    calls = 0

    def listen() -> FakeConsentListener:
        nonlocal calls
        calls += 1
        if calls == 1:
            world.access.start_grant("internal_notify")  # the concurrent click registers first
        return real_listen()

    world.access._listen = listen  # type: ignore[method-assign]

    world.access.start_grant("internal_notify")

    concurrent, first = world.listeners  # the concurrent click built its listener first
    assert concurrent.closed  # overwritten: cancelled and closed, not left bound
    assert not first.closed
    assert len(world.jobs) == 2
    world.run_jobs()  # the overwritten flow's job finds itself superseded: no outcome

    assert world.access.grant_status("internal_notify").outcome == ConsentOutcome(
        ConsentFailure.TIMED_OUT
    )


def test_a_second_grant_click_supersedes_the_waiting_flow(world: World) -> None:
    world.import_client()
    world.next_answer = ANSWER
    world.access.start_grant("internal_notify")
    first = world.listeners[0]

    world.next_answer = None
    world.access.start_grant("internal_notify")

    assert first.closed and len(world.listeners) == 2 and len(world.jobs) == 2
    first_job, second_job = world.jobs
    world.jobs.clear()
    first_job()  # Google answered the first flow; it exchanges, then finds itself superseded

    assert world.oauth.calls == [("exchange", CODE)]
    assert world.changes() == []
    status = world.access.grant_status("internal_notify")
    assert status.waiting_until is not None and status.outcome is None  # the second waits

    second_job()

    assert world.access.grant_status("internal_notify").outcome == ConsentOutcome(
        ConsentFailure.TIMED_OUT
    )


def test_removing_cancels_a_waiting_flow(world: World) -> None:
    world.import_client()
    world.access.start_grant("internal_notify")

    world.access.remove_grant("internal_notify")
    world.run_jobs()

    assert world.listeners[0].closed
    status = world.access.grant_status("internal_notify")
    assert status.waiting_until is None and status.outcome is None


def test_an_unexpected_error_while_completing_is_crashed(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    world.import_client()

    def exchange(*args: object) -> None:
        raise RuntimeError(f"boom {CODE}")

    world.oauth.exchange = exchange  # type: ignore[method-assign,assignment]

    status = world.grant()

    assert status.outcome == ConsentOutcome(ConsentFailure.CRASHED)
    assert "google_consent_crashed" in [r.getMessage() for r in caplog.records]
    assert all(r.exc_info is None for r in google_records(caplog))
    assert world.listeners[0].closed


# Row: Remove ------------------------------------------------------------------------------------


def test_removing_revokes_then_deletes_the_token_and_its_meta(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    world.import_client()
    world.grant(tokens=token_set("ya29.cached", refresh_token="1//r"))
    world.secrets.calls.clear()

    removal = world.access.remove_grant("internal_notify")

    assert removal.revocation is Revocation.REVOKED
    assert world.oauth.revoked == ["1//r"]
    assert world.changes() == [
        ("delete", "google/internal_notify"),
        ("delete", "meta/google/internal_notify"),
    ]
    assert removal.status.state is GrantState.NOT_GRANTED and removal.status.outcome is None
    assert "google_grant_removed" in [r.getMessage() for r in caplog.records]
    with pytest.raises(AdapterError) as caught:
        world.access.access_token("internal_notify")  # the cached token is gone too
    assert caught.value.code == "google_not_granted"


def test_removing_when_google_cannot_be_reached_still_deletes(world: World) -> None:
    world.import_client()
    world.put_grant("internal_notify", "1//r", MAC)
    world.oauth.revoke_error = AdapterError(True, "google_unavailable", "down")

    removal = world.access.remove_grant("internal_notify")

    assert removal.revocation is Revocation.FAILED
    assert "google/internal_notify" not in world.stored()
    assert "meta/google/internal_notify" not in world.stored()


def test_removing_a_grant_whose_account_postmaster_uses_does_not_revoke(world: World) -> None:
    world.import_client()
    world.put_grant("internal_notify", "1//internal", MAC)
    world.put_grant("postmaster", "1//postmaster", MAC.upper())

    removal = world.access.remove_grant("internal_notify")

    assert removal.revocation is Revocation.SHARED
    assert world.oauth.revoked == []
    assert world.stored()["google/postmaster"] == "1//postmaster"


def test_removing_nothing_revokes_nothing(world: World) -> None:
    removal = world.access.remove_grant("postmaster")

    assert removal.revocation is None and world.oauth.calls == []


def test_a_keyring_failure_while_removing_raises(world: World) -> None:
    world.put_grant("internal_notify", "1//r", MAC)
    world.secrets.failing = {"delete"}

    with pytest.raises(CredentialStoreFailed) as caught:
        world.access.remove_grant("internal_notify")

    assert caught.value.key == "google_internal_notify"


# Row: Refresh -----------------------------------------------------------------------------------


@pytest.fixture
def granted(world: World) -> World:
    world.import_client()
    world.grant(tokens=token_set("ya29.first", refresh_token="1//stored", expires_in=3600))
    world.oauth.calls.clear()
    return world


def test_a_valid_cached_token_is_used_without_calling_google(granted: World) -> None:
    granted.clock.advance(timedelta(seconds=3600 - 61))

    assert granted.access.access_token("internal_notify") == "ya29.first"
    assert granted.oauth.calls == []


def test_an_expiring_token_is_refreshed_and_cached(granted: World) -> None:
    granted.clock.advance(timedelta(seconds=3600 - 60))
    granted.oauth.refreshes.append(token_set("ya29.second", refresh_token=None))

    assert granted.access.access_token("internal_notify") == "ya29.second"
    assert granted.access.access_token("internal_notify") == "ya29.second"
    assert granted.oauth.calls == [("refresh", "1//stored")]
    assert granted.oauth.clients[-1] == CLIENT
    assert "ya29.second" not in granted.stored().values()


def test_invalid_grant_on_refresh_marks_the_grant_and_stops_calling_google(
    granted: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    granted.clock.advance(timedelta(hours=2))
    granted.oauth.refreshes.append(AdapterError(False, "google_invalid_grant", "refused"))

    with pytest.raises(AdapterError) as caught:
        granted.access.access_token("internal_notify")

    assert (caught.value.code, caught.value.retryable) == ("google_needs_reconsent", False)
    meta = decode_grant_meta(granted.stored()["meta/google/internal_notify"])
    assert meta == GrantMeta(MAC, NOW, NOW + timedelta(hours=2))
    status = granted.access.grant_status("internal_notify")
    assert (status.state, status.needs_reconsent_at) == (
        GrantState.NEEDS_RECONSENT,
        NOW + timedelta(hours=2),
    )
    assert "google_grant_needs_reconsent" in [r.getMessage() for r in caplog.records]

    with pytest.raises(AdapterError) as again:
        granted.access.access_token("internal_notify")

    assert again.value.code == "google_needs_reconsent"
    assert granted.oauth.calls == [("refresh", "1//stored")]  # no further Google calls


def test_a_token_replaced_while_refreshing_is_not_marked(granted: World) -> None:
    granted.clock.advance(timedelta(hours=2))

    def refresh(client: ClientConfig, refresh_token: str) -> object:
        granted.put("google/internal_notify", "1//replaced")  # a re-grant landed meanwhile
        raise AdapterError(False, "google_invalid_grant", "refused")

    granted.oauth.refresh = refresh  # type: ignore[method-assign,assignment]

    with pytest.raises(AdapterError):
        granted.access.access_token("internal_notify")

    assert decode_grant_meta(granted.stored()["meta/google/internal_notify"]) == GrantMeta(MAC, NOW)


def test_a_refreshed_token_is_not_cached_once_its_refresh_token_is_gone(
    granted: World,
) -> None:
    granted.clock.advance(timedelta(hours=2))
    refreshes = 0

    def refresh(client: ClientConfig, refresh_token: str) -> object:
        nonlocal refreshes
        refreshes += 1
        if refreshes == 1:
            granted.put("google/internal_notify", "1//replaced")  # a re-grant landed meanwhile
        return token_set(f"ya29.refreshed-{refreshes}")

    granted.oauth.refresh = refresh  # type: ignore[method-assign,assignment]

    assert granted.access.access_token("internal_notify") == "ya29.refreshed-1"
    assert granted.access.access_token("internal_notify") == "ya29.refreshed-2"  # not cached
    assert refreshes == 2
    assert granted.access.access_token("internal_notify") == "ya29.refreshed-2"  # now cached
    assert refreshes == 2


def test_other_refresh_errors_propagate_unchanged(granted: World) -> None:
    granted.clock.advance(timedelta(hours=2))
    failure = AdapterError(True, "google_unavailable", "down")
    granted.oauth.refreshes.append(failure)

    with pytest.raises(AdapterError) as caught:
        granted.access.access_token("internal_notify")

    assert caught.value is failure
    assert granted.access.grant_status("internal_notify").state is GrantState.GRANTED


def test_no_client_or_no_grant_fails_without_calling_google(world: World) -> None:
    with pytest.raises(AdapterError) as no_client:
        world.access.access_token("internal_notify")
    world.import_client()
    with pytest.raises(AdapterError) as no_grant:
        world.access.access_token("internal_notify")

    assert (no_client.value.code, no_client.value.retryable) == (
        "google_client_not_imported",
        False,
    )
    assert (no_grant.value.code, no_grant.value.retryable) == ("google_not_granted", False)
    assert world.oauth.calls == []


# check_stored_grant (the health panel: no call to Google) --------------------------------------


def test_a_stored_grant_checks_out_without_calling_google(world: World) -> None:
    world.import_client()
    world.put_grant("internal_notify", "1//stored", MAC)
    before = dict(world.secrets.store)

    assert world.access.check_stored_grant("internal_notify") is None

    assert world.oauth.calls == []
    assert world.secrets.store == before
    assert [operation for operation, _ in world.secrets.calls] == ["read"] * 3
    assert world.access.grant_status("internal_notify").state is GrantState.GRANTED


def test_check_stored_grant_raises_each_code_without_calling_google(world: World) -> None:
    def code_of() -> tuple[str, bool]:
        with pytest.raises(AdapterError) as caught:
            world.access.check_stored_grant("internal_notify")
        return caught.value.code, caught.value.retryable

    assert code_of() == ("google_client_not_imported", False)
    world.import_client()
    assert code_of() == ("google_not_granted", False)
    world.put("google/internal_notify", "1//stored")
    world.put(
        "meta/google/internal_notify",
        encode_grant_meta(GrantMeta(MAC, NOW - timedelta(days=1), NOW - timedelta(hours=1))),
    )
    assert code_of() == ("google_needs_reconsent", False)
    world.secrets.failing.add("read")
    assert code_of() == ("keyring_failed", False)
    assert world.oauth.calls == []


def test_check_stored_grant_ignores_a_cached_token_and_caches_nothing(granted: World) -> None:
    granted.put(
        "meta/google/internal_notify",
        encode_grant_meta(GrantMeta(MAC, NOW, NOW)),  # marked since the token was cached
    )

    with pytest.raises(AdapterError) as caught:
        granted.access.check_stored_grant("internal_notify")

    assert caught.value.code == "google_needs_reconsent"
    assert granted.oauth.calls == []
    # access_token is unchanged: it still answers from its cache while the token is valid
    assert granted.access.access_token("internal_notify") == "ya29.first"


def test_check_stored_grant_refuses_an_unknown_grant(world: World) -> None:
    with pytest.raises(UnknownGrant):
        world.access.check_stored_grant("calendar")


def test_the_access_token_never_reaches_the_keyring(granted: World) -> None:
    granted.clock.advance(timedelta(hours=2))
    granted.oauth.refreshes.append(token_set("ya29.third"))
    granted.access.access_token("internal_notify")

    for value in ("ya29.first", "ya29.third"):
        assert value not in granted.stored().values()
        assert all(value not in stored for stored in granted.secrets.store.values())


# Statuses ---------------------------------------------------------------------------------------


def test_statuses_follow_the_grants_order_and_an_unreadable_keyring_is_unknown(
    world: World,
) -> None:
    world.put_grant("postmaster", "1//p", "x@gmail.com")

    statuses = world.access.grant_statuses()

    assert [s.spec.grant for s in statuses] == ["internal_notify", "postmaster"]
    assert [s.state for s in statuses] == [GrantState.NOT_GRANTED, GrantState.GRANTED]
    world.secrets.failing = {"read"}
    assert [s.state for s in world.access.grant_statuses()] == [GrantState.UNKNOWN] * 2


def test_a_token_without_a_readable_meta_is_granted_with_nothing_known(world: World) -> None:
    world.put("google/postmaster", "1//p")
    world.put("meta/google/postmaster", "garbled")

    status = world.access.grant_status("postmaster")

    assert (status.state, status.account, status.granted_at) == (GrantState.GRANTED, None, None)


# The locks --------------------------------------------------------------------------------------


class OrderedLock:
    """A lock that records, per thread, which named locks are held when it is taken."""

    def __init__(
        self, name: str, held: dict[int, list[str]], log: list[tuple[str, tuple[str, ...]]]
    ) -> None:
        self.name = name
        self._lock = threading.Lock()
        self._held = held
        self._log = log

    def __enter__(self) -> "OrderedLock":
        mine = self._held.setdefault(threading.get_ident(), [])
        self._log.append((self.name, tuple(mine)))
        self._lock.acquire()
        mine.append(self.name)
        return self

    def __exit__(self, *exc: object) -> None:
        self._held[threading.get_ident()].remove(self.name)
        self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()


Taken = list[tuple[str, tuple[str, ...]]]  # (lock, the locks this thread held then)


@pytest.fixture
def instrumented(world: World) -> Iterator[tuple[World, Taken, dict[int, list[str]]]]:
    held: dict[int, list[str]] = {}
    taken: Taken = []
    world.access._state_lock = OrderedLock("state", held, taken)  # type: ignore[assignment]
    world.access._store_lock = OrderedLock("store", held, taken)  # type: ignore[assignment]
    yield world, taken, held


def test_the_store_lock_comes_first_and_the_state_lock_is_never_held_over_io(
    instrumented: tuple[World, Taken, dict[int, list[str]]],
) -> None:
    world, taken, held = instrumented
    io_with_state: list[str] = []

    def watch(target: object, name: str) -> None:
        real = getattr(target, name)

        def call(*args: object, **kwargs: object) -> object:
            if "state" in held.get(threading.get_ident(), []):
                io_with_state.append(name)
            return real(*args, **kwargs)

        setattr(target, name, call)

    for name in ("read", "write", "delete"):
        watch(world.secrets, name)
    for name in ("exchange", "refresh", "revoke"):
        watch(world.oauth, name)
    world.import_client()
    world.put_grant("internal_notify", "1//old-a", "a@nextronai.com")

    world.grant(tokens=token_set(email="b@nextronai.com", refresh_token="1//b", expires_in=60))
    world.grant("postmaster", token_set(scope="openid email"))  # refused, revoked
    world.oauth.refreshes.append(AdapterError(False, "google_invalid_grant", "refused"))
    with pytest.raises(AdapterError):
        world.access.access_token("internal_notify")
    world.access.grant_statuses()
    world.access.remove_grant("internal_notify")

    assert io_with_state == []
    assert ("store", ("state",)) not in taken  # never the store lock while holding the state lock
    assert ("state", ("store",)) in taken  # both are taken: the store lock first
    assert all(name in ("state", "store") for name, _ in taken)


# End to end on the real listener and a real thread ---------------------------------------------


def test_a_flow_completes_on_the_real_loopback_listener_and_a_daemon_thread() -> None:
    """``start_daemon`` and ``LoopbackListener``, as ``main.build_app`` wires them: the browser
    stand-in plays Google, sending the redirect to the listener from its own thread."""
    import http.client  # noqa: PLC0415
    import time  # noqa: PLC0415

    from nextron.adapters.google_oauth import LoopbackListener  # noqa: PLC0415

    secrets = InMemorySecrets("test")
    secrets.store[(SERVICE, g.CLIENT_SECRET_NAME)] = encode_client_config(CLIENT)
    oauth = FakeGoogleOAuth()
    oauth.exchanges.append(token_set("ya29.live", refresh_token="1//live"))
    redirected: list[int] = []

    def google(url: str) -> bool:
        query = dict(parse_qsl(urlsplit(url).query))
        target = urlsplit(query["redirect_uri"])

        def redirect() -> None:
            connection = http.client.HTTPConnection(target.hostname, target.port, timeout=5)
            connection.request("GET", f"/?state={query['state']}&code={CODE}")
            redirected.append(connection.getresponse().status)
            connection.close()

        threading.Thread(target=redirect, daemon=True).start()
        return True

    access = GoogleAccess(
        secrets,
        FrozenClock(NOW),
        oauth=oauth,
        listen=LoopbackListener,
        files=FakeClientFiles(),
        browser=google,
        env="dev",
        allowlist=lambda: [MAC],
    )

    access.start_grant("internal_notify")
    deadline = time.monotonic() + 10
    while access.grant_status("internal_notify").waiting_until is not None:
        assert time.monotonic() < deadline, "the flow never completed"
        time.sleep(0.02)

    status = access.grant_status("internal_notify")
    assert (status.state, status.account, status.outcome) == (
        GrantState.GRANTED,
        MAC,
        ConsentOutcome(None),
    )
    assert redirected == [200]
    assert oauth.calls == [("exchange", CODE)]
    assert secrets.store[(SERVICE, "google/internal_notify")] == "1//live"
    assert access.access_token("internal_notify") == "ya29.live"
