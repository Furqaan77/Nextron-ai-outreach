"""Unsubscribe tokens on a temporary database at head (FR-70, AD-15, AD-17, Story 3.8):
``Compliance.issue_unsub_token`` through every row of the spec's I/O matrix (issue, repeat,
active kid 2, key missing, bad arguments) with the real ``TokenCodec`` and ``FrozenClock``; the
``unsub_tokens`` table at head and its triggers and checks against raw SQL; the
``queries.compliance`` readers; ``Credentials``' unsubscribe-key methods (save, bad key, remove
in use, remove retired, the listing and its roles, the database read before any keyring write);
and no address, token, key or address hash in any table or log record."""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.adapters.crypto.unsub_token import TokenCodec
from nextron.app.compliance.api import Compliance
from nextron.app.ops.api import Recovery
from nextron.app.queries import compliance as compliance_queries
from nextron.app.setup import api as setup_api
from nextron.app.setup.api import (
    CredentialState,
    Credentials,
    CredentialStoreFailed,
    Setup,
    UnsubKeyRole,
    UnsubKeyStatus,
)
from nextron.db.owners import TABLES
from nextron.db.pii_registry import PII, PiiClass
from nextron.db.uow import UnitOfWorkFactory, current, in_transaction, use_case
from nextron.domain.contactability import key_hash
from nextron.domain.credentials import InvalidCredentialValue, unsub_key
from nextron.domain.identity import IdentityError
from nextron.domain.settings_schema import Key
from nextron.domain.unsub_token import (
    DecodedToken,
    TokenAlreadyIssued,
    UnsubKeyInUse,
    UnsubKeysHeldInRecovery,
    encode_key,
    token_hash,
)
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import KEYRING_FAILED
from nextron.ports.token_codec import UNSUB_KEY_MISSING
from nextron.startup_checks import CheckResult, Status, check_unsub_token_keys
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.token_codec import FakeTokenCodec
from tests.migration_chains import run_sql

T0 = datetime(2026, 11, 18, 9, 30, 15, 987654, tzinfo=UTC)
T0_SECOND = datetime(2026, 11, 18, 9, 30, 15, tzinfo=UTC)
SERVICE = "nextron-engine/test"
OM_1 = "01JD3QZ8X5V9K2M7N4P6R8T0AA"
OM_2 = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
OM_3 = "01JD3QZ8X5V9K2M7N4P6R8T0AC"
ADDRESS = "Jane.Doe+Offers@Firm.co.uk"
PERMANENT = "unsubscribe token records are permanent"
TOKEN_COLUMNS = "id, token_hash, om_id, kid, issued_at"
KEYS = {  # test keys, never deployed
    (kid, key): encode_key(bytes([kid * 16 + index]) * 32)
    for kid in range(1, 7)
    for index, key in enumerate(("k_tok", "k_edge"))
}
COMPLIANCE_LOGGERS = ("nextron.app.compliance", "nextron.adapters.crypto")


@dataclass(frozen=True)
class StandInMessage:
    """An OutboundMessage as ``issue_unsub_token`` reads it (Epic 5 builds the real one)."""

    id: Any
    to_address: Any


@dataclass
class World:
    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    secrets: InMemorySecrets
    compliance: Compliance
    credentials: Credentials

    def transact[R](self, fn: Callable[[], R]) -> R:
        """Run ``fn`` inside a caller's use case and transaction, as Story 5.2's enqueue will."""

        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def read[R](self, fn: Callable[[], R]) -> R:
        with self.units.open(read_only=True):
            return fn()

    def issue(self, om_id: str = OM_1, address: Any = ADDRESS) -> str:
        message = StandInMessage(om_id, address)
        return self.transact(lambda: self.compliance.issue_unsub_token(message))

    def issued_at(self, at: datetime, om_id: str, kid: int | None = None) -> str:
        """A token for ``om_id`` issued at ``at`` (under ``kid`` when given)."""
        if kid is not None:
            self.set_active_kid(kid)
        self.clock.set(at)
        try:
            return self.issue(om_id)
        finally:
            self.clock.set(T0)

    def set_active_kid(self, kid: int) -> None:
        with self.units.open():
            Setup(self.clock).set(Key.UNSUB_ACTIVE_KID, kid)

    def set_keys(self, kid: int, *keys: str) -> None:
        for key in keys or ("k_tok", "k_edge"):
            self.secrets.store[(SERVICE, f"unsub/{key}/{kid}")] = KEYS[(kid, key)]

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        connection = sqlite3.connect(self.db_file, isolation_level=None)
        try:
            return [tuple(row) for row in connection.execute(statement, params).fetchall()]
        finally:
            connection.close()

    @property
    def db_file(self) -> Path:
        assert self.engine.url.database
        return Path(self.engine.url.database)

    def tokens(self) -> list[tuple[Any, ...]]:
        return self.sql(f"SELECT {TOKEN_COLUMNS} FROM unsub_tokens ORDER BY rowid")

    def dump(self) -> str:
        """Every row of every table, as one text."""
        tables = [name for (name,) in self.sql("SELECT name FROM sqlite_master WHERE type='table'")]
        return repr([self.sql(f"SELECT * FROM {table}") for table in sorted(tables)])


@pytest.fixture
def world(units: UnitOfWorkFactory, db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    secrets = InMemorySecrets()
    found = World(
        units,
        db_engine,
        clock,
        secrets,
        Compliance(clock, codec=TokenCodec(secrets)),
        Credentials(secrets, clock),
    )
    found.set_keys(1)
    return found


# Issue ------------------------------------------------------------------------------------------


def test_issue_returns_a_token_records_its_hash_and_decodes_to_the_address_hash(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    token = world.issue()

    assert len(token) == 108
    [(row_id, stored_hash, om_id, kid, issued_at)] = world.tokens()
    assert (stored_hash, om_id, kid) == (token_hash(token), OM_1, 1)
    assert issued_at == "2026-11-18T09:30:15.000000Z"  # the clock's second
    assert len(row_id) == 26
    decoded = TokenCodec(world.secrets).decode(token)
    assert decoded == DecodedToken(
        1, bytes.fromhex(key_hash("address", ADDRESS)), int(T0_SECOND.timestamp())
    )
    assert world.sql("SELECT count(*) FROM events") == [(0,)]  # no events row
    assert not [r for r in caplog.records if r.name.startswith(COMPLIANCE_LOGGERS)]


def test_no_address_token_key_or_address_hash_reaches_any_table_or_log(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    token = world.issue()

    dumped = world.dump()
    secret_texts = [
        ADDRESS,
        ADDRESS.lower(),
        "jane.doe",
        token,
        key_hash("address", ADDRESS),
        KEYS[(1, "k_tok")],
        KEYS[(1, "k_edge")],
    ]
    logged = "\n".join(f"{r.getMessage()} {vars(r)}" for r in caplog.records)
    for text in secret_texts:
        assert text not in dumped
        assert text not in logged
    assert token_hash(token) in dumped  # only the token's hash is stored


def test_a_repeat_raises_before_any_keyring_read_and_records_nothing(world: World) -> None:
    world.issue()
    before = world.tokens()
    world.secrets.calls.clear()

    with pytest.raises(TokenAlreadyIssued, match="token already issued for this message"):
        world.issue(OM_1, "someone.else@example.com")

    assert world.secrets.calls == []
    assert world.tokens() == before


def test_active_kid_2_encodes_under_kid_2(world: World) -> None:
    world.set_keys(2)
    world.set_active_kid(2)

    token = world.issue()

    decoded = TokenCodec(world.secrets).decode(token)
    assert isinstance(decoded, DecodedToken) and decoded.kid == 2
    assert world.tokens()[0][3] == 2
    assert (
        world.read(lambda: compliance_queries.newest_unsub_token(2)),
        world.read(lambda: compliance_queries.newest_unsub_token(1)),
    ) == (T0_SECOND, None)


@pytest.mark.parametrize("missing", ["k_edge", "k_tok"])
def test_a_missing_key_dooms_the_whole_transaction(world: World, missing: str) -> None:
    del world.secrets.store[(SERVICE, f"unsub/{missing}/1")]

    def two_messages() -> None:
        world.secrets.store[(SERVICE, f"unsub/{missing}/1")] = KEYS[(1, missing)]
        world.compliance.issue_unsub_token(StandInMessage(OM_2, ADDRESS))  # works
        del world.secrets.store[(SERVICE, f"unsub/{missing}/1")]
        world.compliance.issue_unsub_token(StandInMessage(OM_1, ADDRESS))  # fails

    with pytest.raises(AdapterError) as refused:
        world.transact(two_messages)

    assert (refused.value.code, refused.value.retryable) == (UNSUB_KEY_MISSING, False)
    assert f"unsub/{missing}/1" in refused.value.message
    assert world.tokens() == []  # the first token's row was rolled back too


def test_a_keyring_failure_propagates_and_records_nothing(world: World) -> None:
    world.secrets.failing.add("read")

    with pytest.raises(AdapterError) as failed:
        world.issue()

    assert failed.value.code == KEYRING_FAILED
    assert world.tokens() == []


@pytest.mark.parametrize(
    ("om_id", "address", "error"),
    [
        ("not-a-ulid", ADDRESS, ValueError),
        (None, ADDRESS, ValueError),
        (OM_1, None, TypeError),
        (OM_1, b"jane@firm.co.uk", TypeError),
    ],
)
def test_bad_arguments_raise_before_any_read(
    world: World, om_id: Any, address: Any, error: type
) -> None:
    with pytest.raises(error):
        world.issue(om_id, address)

    assert world.secrets.calls == []
    assert world.tokens() == []


def test_an_address_the_normaliser_refuses_records_nothing(world: World) -> None:
    with pytest.raises(IdentityError):
        world.issue(OM_1, "not an address")

    assert world.secrets.calls == []
    assert world.tokens() == []


def test_without_a_codec_issuing_raises_type_error(world: World) -> None:
    bare = Compliance(world.clock)

    with pytest.raises(TypeError, match="codec"):
        world.transact(lambda: bare.issue_unsub_token(StandInMessage(OM_1, ADDRESS)))

    assert world.tokens() == []


def test_the_fake_codec_serves_the_command_too(world: World) -> None:
    fake = FakeTokenCodec()
    compliance = Compliance(world.clock, codec=fake)

    token = world.transact(lambda: compliance.issue_unsub_token(StandInMessage(OM_3, ADDRESS)))

    assert fake.encoded == [
        (1, bytes.fromhex(key_hash("address", ADDRESS)), int(T0_SECOND.timestamp()))
    ]
    assert world.tokens()[0][1] == token_hash(token)


# The queries --------------------------------------------------------------------------------------


def test_the_newest_token_of_each_kid(world: World) -> None:
    world.set_keys(3)
    assert world.read(compliance_queries.newest_unsub_tokens) == {}
    assert world.read(lambda: compliance_queries.newest_unsub_token(1)) is None

    world.issued_at(T0 - timedelta(days=10), OM_1)
    world.issued_at(T0 - timedelta(days=2), OM_2)
    world.issued_at(T0 - timedelta(days=5), OM_3, kid=3)

    assert dict(world.read(compliance_queries.newest_unsub_tokens)) == {
        1: (T0 - timedelta(days=2)).replace(microsecond=0),
        3: (T0 - timedelta(days=5)).replace(microsecond=0),
    }
    assert world.read(lambda: compliance_queries.newest_unsub_token(1)) == (
        T0 - timedelta(days=2)
    ).replace(microsecond=0)


# The table at head ------------------------------------------------------------------------------


def test_the_table_has_its_columns_checks_indexes_and_triggers(world: World) -> None:
    columns = world.sql("SELECT name, type, \"notnull\", pk FROM pragma_table_info('unsub_tokens')")
    assert columns == [
        ("id", "VARCHAR(26)", 1, 1),
        ("token_hash", "VARCHAR(64)", 1, 0),
        ("om_id", "VARCHAR(26)", 1, 0),
        ("kid", "INTEGER", 1, 0),
        ("issued_at", "VARCHAR(27)", 1, 0),
    ]
    [(table_sql,)] = world.sql("SELECT sql FROM sqlite_master WHERE name = 'unsub_tokens'")
    for check in ("ck_unsub_tokens_token_hash", "ck_unsub_tokens_kid", "ck_unsub_tokens_issued_at"):
        assert f"CONSTRAINT {check} CHECK" in table_sql
    indexes = world.sql(
        "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'unsub_tokens' "
        "AND sql IS NOT NULL ORDER BY name"
    )
    assert indexes == [
        (
            "ix_unsub_tokens_kid_issued_at",
            "CREATE INDEX ix_unsub_tokens_kid_issued_at ON unsub_tokens (kid, issued_at)",
        ),
        (
            "uq_unsub_tokens_om_id",
            "CREATE UNIQUE INDEX uq_unsub_tokens_om_id ON unsub_tokens (om_id)",
        ),
        (
            "uq_unsub_tokens_token_hash",
            "CREATE UNIQUE INDEX uq_unsub_tokens_token_hash ON unsub_tokens (token_hash)",
        ),
    ]
    triggers = world.sql(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'unsub_tokens' "
        "ORDER BY name"
    )
    assert triggers == [
        ("trg_unsub_tokens_immutable",),
        ("trg_unsub_tokens_no_delete",),
        ("trg_unsub_tokens_no_reuse",),
    ]
    names = ["id", "token_hash", "om_id", "kid", "issued_at"]
    assert TABLES["unsub_tokens"] == {"compliance": tuple(names)}
    assert PII["unsub_tokens"] == dict.fromkeys(names, PiiClass.NONE)


ROW_ID = "01JD3QZ8X5V9K2M7N4P6R8T0AD"
HASH = "a" * 64
STAMP = "2026-11-18T09:30:15.000000Z"


def _insert(
    row_id: str = ROW_ID,
    hash_: str = HASH,
    om: str = OM_2,
    kid: object = 1,
    at: str = STAMP,
    verb: str = "INSERT",
) -> str:
    return (
        f"{verb} INTO unsub_tokens ({TOKEN_COLUMNS}) "
        f"VALUES ('{row_id}', '{hash_}', '{om}', {kid}, '{at}')"
    )


REFUSED = [
    "DELETE FROM unsub_tokens",
    f"DELETE FROM unsub_tokens WHERE id = '{ROW_ID}'",
    f"UPDATE unsub_tokens SET kid = 2 WHERE id = '{ROW_ID}'",
    f"UPDATE unsub_tokens SET issued_at = '2020-01-01T00:00:00.000000Z' WHERE id = '{ROW_ID}'",
    f"UPDATE unsub_tokens SET token_hash = '{'b' * 64}'",
    f"UPDATE unsub_tokens SET om_id = '{OM_3}'",
    f"UPDATE unsub_tokens SET id = id",  # even a no-op update
    _insert(),  # the same row again
    _insert(row_id="01JD3QZ8X5V9K2M7N4P6R8T0AE"),  # its token_hash and om_id again
    _insert(row_id="01JD3QZ8X5V9K2M7N4P6R8T0AE", om=OM_3),  # its token_hash again
    _insert(row_id="01JD3QZ8X5V9K2M7N4P6R8T0AE", hash_="b" * 64),  # its om_id again
    _insert(verb="INSERT OR REPLACE"),
    _insert(hash_="b" * 64, om=OM_3, verb="REPLACE"),  # its id again
]


@pytest.mark.parametrize("statement", REFUSED)
def test_the_triggers_make_every_record_permanent(world: World, statement: str) -> None:
    run_sql(world.db_file, _insert())
    before = world.tokens()

    with pytest.raises(sqlite3.IntegrityError, match=PERMANENT):
        run_sql(world.db_file, statement)

    assert world.tokens() == before


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (_insert(kid=0), "ck_unsub_tokens_kid"),
        (_insert(kid=256), "ck_unsub_tokens_kid"),
        (_insert(hash_="A" * 64), "ck_unsub_tokens_token_hash"),
        (_insert(hash_="a" * 63), "ck_unsub_tokens_token_hash"),
        (_insert(at="2026-11-18T09:30:15.000001Z"), "ck_unsub_tokens_issued_at"),
        (
            f"INSERT INTO unsub_tokens (id, token_hash, om_id, kid) "
            f"VALUES ('{ROW_ID}', '{HASH}', '{OM_2}', 1)",
            "NOT NULL",
        ),
    ],
)
def test_the_checks_refuse_a_bad_row(world: World, statement: str, constraint: str) -> None:
    with pytest.raises(sqlite3.IntegrityError, match=constraint):
        run_sql(world.db_file, statement)

    assert world.tokens() == []


# Credentials: the unsubscribe token keys -------------------------------------------------------

GOOD_KEY = encode_key(bytes(range(32)))


def statuses(world: World, kid: int) -> tuple[UnsubKeyStatus, ...]:
    return tuple(world.credentials.unsub_key_status(kid, key) for key in ("k_tok", "k_edge"))


def test_save_stores_the_canonical_key_then_its_time(world: World) -> None:
    status = world.read(lambda: world.credentials.save_unsub_key(4, "k_tok", f"  {GOOD_KEY}=\n"))

    assert (status.state, status.valid, status.kid) == (CredentialState.SET, True, 4)
    assert status.updated_at == T0
    assert world.secrets.store[(SERVICE, "unsub/k_tok/4")] == GOOD_KEY  # canonical, unpadded
    assert world.secrets.store[(SERVICE, "meta/unsub/k_tok/4")] == "2026-11-18T09:30:15.987654Z"
    writes = [call for call in world.secrets.calls if call[0] == "write"]
    assert writes == [("write", "unsub/k_tok/4"), ("write", "meta/unsub/k_tok/4")]


@pytest.mark.parametrize(
    "text",
    [
        encode_key(bytes(32))[:-1],  # 31-ish: 42 characters
        "QUJD" * 10 + "QUJ",  # 43 characters that aren't canonical (low bits set)
        "+" * 43,  # standard base64
        GOOD_KEY[:20] + " " + GOOD_KEY[21:],  # a space inside
        "",
        "   ",
    ],
    ids=["short", "noncanonical", "standard", "space_inside", "empty", "blank"],
)
def test_a_bad_key_is_refused_and_nothing_is_read_or_stored(world: World, text: str) -> None:
    with pytest.raises(InvalidCredentialValue) as refused:
        world.read(lambda: world.credentials.save_unsub_key(1, "k_edge", text))

    assert refused.value.key == "k_edge"
    assert text.strip() == "" or "32-byte key in base64url" in refused.value.reason
    assert world.secrets.store[(SERVICE, "unsub/k_edge/1")] == KEYS[(1, "k_edge")]
    assert not [call for call in world.secrets.calls if call[0] != "read"]


def test_remove_while_a_token_is_under_two_years_old_is_refused(world: World) -> None:
    world.issued_at(T0 - timedelta(days=365), OM_1)
    world.secrets.calls.clear()

    for key in ("k_tok", "k_edge"):
        with pytest.raises(UnsubKeyInUse, match="Key 1 still has tokens under 2 years old"):
            world.read(lambda key=key: world.credentials.remove_unsub_key(1, key))

    assert world.secrets.calls == []  # nothing read, nothing deleted
    assert all(state.state is CredentialState.SET for state in statuses(world, 1))


def test_remove_after_the_newest_token_is_732_days_old_deletes_both_entries(
    world: World,
) -> None:
    world.issued_at(T0 - timedelta(days=800), OM_1)
    world.issued_at(T0 - timedelta(days=732), OM_2)
    world.read(lambda: world.credentials.save_unsub_key(5, "k_tok", GOOD_KEY))  # a meta entry

    removed = world.read(lambda: world.credentials.remove_unsub_key(1, "k_tok"))

    assert (removed.state, removed.updated_at) == (CredentialState.NOT_SET, None)
    assert (SERVICE, "unsub/k_tok/1") not in world.secrets.store
    assert (SERVICE, "meta/unsub/k_tok/1") not in world.secrets.store
    assert (SERVICE, "unsub/k_edge/1") in world.secrets.store  # only the one key


def test_replacing_a_set_key_in_use_is_refused_but_entering_a_missing_one_is_not(
    world: World,
) -> None:
    world.issued_at(T0 - timedelta(days=100), OM_1)

    with pytest.raises(UnsubKeyInUse):
        world.read(lambda: world.credentials.save_unsub_key(1, "k_tok", GOOD_KEY))
    assert world.secrets.store[(SERVICE, "unsub/k_tok/1")] == KEYS[(1, "k_tok")]

    # restored from escrow after a loss: the entry is missing, so it can always be entered
    del world.secrets.store[(SERVICE, "unsub/k_tok/1")]
    world.read(lambda: world.credentials.save_unsub_key(1, "k_tok", KEYS[(1, "k_tok")]))
    # a garbled entry decodes nothing, so it can be replaced too
    world.secrets.store[(SERVICE, "unsub/k_edge/1")] = "garbled"
    world.read(lambda: world.credentials.save_unsub_key(1, "k_edge", KEYS[(1, "k_edge")]))
    assert all(found.valid for found in statuses(world, 1))


def test_replacing_a_key_whose_tokens_are_two_years_old_is_allowed(world: World) -> None:
    world.issued_at(T0 - timedelta(days=731), OM_1)

    world.read(lambda: world.credentials.save_unsub_key(1, "k_tok", GOOD_KEY))

    assert world.secrets.store[(SERVICE, "unsub/k_tok/1")] == GOOD_KEY


def test_the_newest_token_is_read_before_any_keyring_write(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[tuple[str, str]]] = []
    real = compliance_queries.newest_unsub_token
    world.secrets.calls.clear()

    def spy(kid: int) -> datetime | None:
        current()  # inside the caller's unit of work
        seen.append(list(world.secrets.calls))
        return real(kid)

    monkeypatch.setattr(setup_api.compliance_queries, "newest_unsub_token", spy)
    world.read(lambda: world.credentials.save_unsub_key(3, "k_tok", GOOD_KEY))
    saved = list(world.secrets.calls)
    world.secrets.calls.clear()
    world.read(lambda: world.credentials.remove_unsub_key(3, "k_tok"))

    assert seen == [[], []]  # read first, before any keyring call
    assert saved == [
        ("read", "unsub/k_tok/3"),
        ("read", "unsub/k_edge/3"),  # the other key, which the new one must differ from
        ("write", "unsub/k_tok/3"),
        ("write", "meta/unsub/k_tok/3"),
    ]
    assert world.secrets.calls == [("delete", "unsub/k_tok/3"), ("delete", "meta/unsub/k_tok/3")]


def test_the_listing_shows_the_active_the_next_and_every_kid_with_tokens(world: World) -> None:
    world.set_keys(3)
    world.set_keys(5)
    world.issued_at(T0 - timedelta(days=300), OM_1, kid=3)
    world.issued_at(T0 - timedelta(days=900), OM_2, kid=5)
    world.set_active_kid(1)

    listing = world.read(world.credentials.unsub_keys)

    assert [(found.kid, found.role) for found in listing] == [
        (1, UnsubKeyRole.ACTIVE),
        (2, UnsubKeyRole.NEXT),
        (3, UnsubKeyRole.IN_USE),
        (5, UnsubKeyRole.RETIRED),
    ]
    assert listing[0].newest_token_at is None
    assert listing[2].newest_token_at == (T0 - timedelta(days=300)).replace(microsecond=0)
    shown = [[(s.credential.key, s.state, s.valid) for s in found.keys] for found in listing[:2]]
    assert shown == [
        [("k_tok", CredentialState.SET, True), ("k_edge", CredentialState.SET, True)],
        [("k_tok", CredentialState.NOT_SET, False), ("k_edge", CredentialState.NOT_SET, False)],
    ]


def test_the_listing_has_no_next_kid_past_255_and_shows_unreadable_keys(world: World) -> None:
    world.set_active_kid(255)
    world.secrets.failing.add("read")

    listing = world.read(world.credentials.unsub_keys)

    assert [(found.kid, found.role) for found in listing] == [(255, UnsubKeyRole.ACTIVE)]
    assert [s.state for s in listing[0].keys] == [CredentialState.UNKNOWN] * 2


def test_a_keyring_failure_while_saving_or_removing_raises_store_failed(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    world.secrets.failing.add("write")
    with pytest.raises(CredentialStoreFailed) as saving:
        world.read(lambda: world.credentials.save_unsub_key(2, "k_tok", GOOD_KEY))
    world.secrets.failing = {"delete"}
    with pytest.raises(CredentialStoreFailed):
        world.read(lambda: world.credentials.remove_unsub_key(2, "k_tok"))

    assert saving.value.key == "unsub/k_tok/2"
    failed = [r for r in caplog.records if r.getMessage() == "unsub_key_store_failed"]
    fields = [
        (r.credential, r.kid, r.operation, r.code)  # type: ignore[attr-defined]
        for r in failed
    ]
    assert fields == [
        ("k_tok", 2, "save", KEYRING_FAILED),
        ("k_tok", 2, "remove", KEYRING_FAILED),
    ]
    assert GOOD_KEY not in caplog.text


def test_saving_logs_the_key_and_kid_never_the_value(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    world.read(lambda: world.credentials.save_unsub_key(2, "k_edge", GOOD_KEY))
    world.read(lambda: world.credentials.remove_unsub_key(2, "k_edge"))

    records = [r for r in caplog.records if r.getMessage().startswith("unsub_key_")]
    logged = [(r.getMessage(), r.credential, r.kid) for r in records]  # type: ignore[attr-defined]
    assert logged == [("unsub_key_saved", "k_edge", 2), ("unsub_key_removed", "k_edge", 2)]
    assert GOOD_KEY not in caplog.text
    assert unsub_key("k_edge").secret_name(2) == "unsub/k_edge/2"



# Review patches (pass 1) ------------------------------------------------------------------------


def test_saving_the_other_keys_value_is_refused_before_any_write(world: World) -> None:
    world.secrets.calls.clear()

    with pytest.raises(InvalidCredentialValue) as refused:
        world.read(lambda: world.credentials.save_unsub_key(1, "k_edge", KEYS[(1, "k_tok")]))

    assert (refused.value.key, refused.value.reason) == (
        "k_edge",
        "k_tok and k_edge of a key id must differ",
    )
    assert not [call for call in world.secrets.calls if call[0] != "read"]
    assert world.secrets.store[(SERVICE, "unsub/k_edge/1")] == KEYS[(1, "k_edge")]


def test_a_lowered_active_kid_leaves_its_old_kid_in_use_not_next(world: World) -> None:
    world.set_keys(2)
    world.issued_at(T0 - timedelta(days=100), OM_1, kid=2)
    world.set_active_kid(1)

    listing = world.read(world.credentials.unsub_keys)

    assert [(found.kid, found.role) for found in listing] == [
        (1, UnsubKeyRole.ACTIVE),
        (2, UnsubKeyRole.IN_USE),
    ]


def test_the_engines_readiness_reads_the_stored_active_kid(world: World) -> None:
    world.set_active_kid(2)
    database = main.EngineDatabase(world.db_file)

    with world.units.open(read_only=True):
        kid = database.unsub_active_kid()
        missing = check_unsub_token_keys(world.secrets.read, database.unsub_active_kid)
    world.set_keys(2)
    with world.units.open(read_only=True):
        present = check_unsub_token_keys(world.secrets.read, database.unsub_active_kid)

    assert kid == 2
    assert missing == CheckResult(
        "unsub_token_keys",
        Status.FAIL,
        "unsubscribe token keys of key id 2: unsub/k_tok/2 is not set; unsub/k_edge/2 is not "
        "set; enter them in Unsubscribe token keys on the Credentials page",
    )
    assert present.status is Status.OK
    assert "unsub/k_tok/2 and unsub/k_edge/2" in present.message


def test_a_failed_read_in_the_in_use_check_raises_store_failed_and_writes_nothing(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    world.issued_at(T0 - timedelta(days=100), OM_1)
    world.secrets.failing.add("read")
    world.secrets.calls.clear()

    with pytest.raises(CredentialStoreFailed) as failed:
        world.read(lambda: world.credentials.save_unsub_key(1, "k_tok", GOOD_KEY))

    assert failed.value.key == "unsub/k_tok/1"
    assert [call for call in world.secrets.calls if call[0] != "read"] == []
    assert world.secrets.store[(SERVICE, "unsub/k_tok/1")] == KEYS[(1, "k_tok")]
    records = [r for r in caplog.records if r.getMessage() == "unsub_key_store_failed"]
    assert [(r.credential, r.kid, r.operation) for r in records] == [  # type: ignore[attr-defined]
        ("k_tok", 1, "save")
    ]
    assert GOOD_KEY not in caplog.text


def test_the_keyring_writes_run_inside_the_read_only_units_transaction(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str, bool]] = []
    for operation in ("write", "delete"):
        real = getattr(world.secrets, operation)

        def recording(name: str, *rest: str, real: Any = real, operation: str = operation) -> Any:
            seen.append((operation, name, in_transaction()))
            return real(name, *rest)

        monkeypatch.setattr(world.secrets, operation, recording)

    world.read(lambda: world.credentials.save_unsub_key(3, "k_tok", GOOD_KEY))
    world.read(lambda: world.credentials.remove_unsub_key(3, "k_tok"))

    assert seen == [
        ("write", "unsub/k_tok/3", True),
        ("write", "meta/unsub/k_tok/3", True),
        ("delete", "unsub/k_tok/3", True),
        ("delete", "meta/unsub/k_tok/3", True),
    ]


# Recovery mode (Story 3.12) ---------------------------------------------------------------------


def turn_recovery_on(world: World) -> None:
    with world.units.open():
        Recovery(world.clock).begin(
            backup_at=T0 - timedelta(days=1), source_name="nextron-test.db", pre_restore_name=None
        )


def test_while_recovery_mode_is_on_a_key_is_neither_removed_nor_replaced(world: World) -> None:
    turn_recovery_on(world)
    world.secrets.calls.clear()
    held = "Key 1 can't be replaced or removed while recovery mode is on after a restore"

    for key in ("k_tok", "k_edge"):
        with pytest.raises(UnsubKeysHeldInRecovery, match=held) as refused:
            world.read(lambda key=key: world.credentials.remove_unsub_key(1, key))
        assert refused.value.code == "unsub_keys_held_in_recovery"
    assert world.secrets.calls == []  # nothing read, nothing deleted
    with pytest.raises(UnsubKeysHeldInRecovery):
        world.read(lambda: world.credentials.save_unsub_key(1, "k_tok", GOOD_KEY))

    assert world.secrets.store[(SERVICE, "unsub/k_tok/1")] == KEYS[(1, "k_tok")]
    assert all(state.state is CredentialState.SET for state in statuses(world, 1))


def test_while_recovery_mode_is_on_a_missing_key_can_still_be_entered(world: World) -> None:
    turn_recovery_on(world)
    del world.secrets.store[(SERVICE, "unsub/k_tok/1")]

    world.read(lambda: world.credentials.save_unsub_key(1, "k_tok", KEYS[(1, "k_tok")]))

    assert world.secrets.store[(SERVICE, "unsub/k_tok/1")] == KEYS[(1, "k_tok")]
