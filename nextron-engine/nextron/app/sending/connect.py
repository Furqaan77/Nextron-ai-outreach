"""Connecting Sending Inboxes through Gmail OAuth (FR-32, FR-56, AD-12, AD-14, AD-4, AD-9; Story
6.2), owned by ``sending``.

``InboxConnections(secrets, clock, *, oauth, listen, browser, gmail, env, sending, open_unit,
start=start_daemon)`` runs the installed-app loopback consent flow of Epic 1 (PKCE S256, a random
``state`` compared in constant time, a one-shot ``127.0.0.1`` listener, ``CONSENT_TIMEOUT``) with
the ``gmail.modify`` scope only (``authorization_url_for_scope``). ``secrets`` is the
``SecretsPort`` (the Desktop client at ``google/client_config``; each inbox's refresh token at
``gmail/<address>``); ``oauth`` the ``GoogleOAuthPort``; ``listen`` builds a
``ConsentListener``; ``browser`` opens the consent URL; ``gmail`` the ``GmailPort``; ``sending``
the ``Sending`` service (its ``connect_inbox`` and ``mark_auth_failed`` use cases);
``open_unit`` the unit-of-work factory's ``open`` (``read_only=True`` for a read). It opens its
own units of work, each in a fresh ``contextvars.Context``, so its callers hold none.

**One flow at a time.** A new connect replaces a waiting flow: its listener is closed and it
records no outcome. The outcome of the last flow is kept in memory, with the address it was for,
until the next connect (``status()``).

- ``start_connect(address, label)`` (the panel's "Connect inbox") and
  ``start_reconnect(inbox_id)`` (a ``connecting`` or ``auth_failed`` inbox's "Connect" or
  "Reconnect") first run the checks that need no Google, in a read-only unit of work, raising
  ``ConnectRefused`` (nothing is started) for the first that fails:

  1. the ``gmail_internal_app_confirmed`` setting (OQ-A2) is ``true``, else ``gate_off``;
  2. ``check_address`` and ``check_label`` (``start_connect``), as their connect failures
     (``brand_domain``, ``role_address``, ``invalid_address``, ``invalid_label``), and the
     address makes a valid keyring name; ``start_reconnect`` reads the inbox instead
     (``unknown_inbox`` for an id that isn't one);
  3. outside ``prod``, the address is in ``sink_allowlist`` (``not_allowlisted``; an allowlist
     that no longer parses is empty);
  4. the Sender's name is set (``sender_name_unset``);
  5. an existing inbox with the address is ``connecting`` or ``auth_failed``
     (``inbox_removed`` for a removed one, ``already_connected`` for an ``active`` or
     ``paused`` one);
  6. the Desktop client is imported (``client_not_imported``; Credential Manager failing is
     ``store_failed``).

  Then, as ``GoogleAccess.start_grant`` does: the waiting flow is cancelled, a listener is
  built (``listener_failed`` as the outcome when it can't be), a ``state`` and a PKCE verifier
  are drawn, the browser opens the consent URL (``browser_failed`` otherwise) and ``start``
  runs the flow's job. Both return ``status()``.

- **The flow's job** waits up to ``CONSENT_TIMEOUT`` for Google's answer, closes the listener,
  then completes, in order (every failure after the exchange revokes the new grant, its refresh
  token or else its access token, and stores nothing: no keyring entry, no row, no status
  change):

  1. no answer is ``timed_out``; a ``state`` other than the flow's (``hmac.compare_digest``) is
     ``state_mismatch``; an ``error`` or no single ``code`` is ``declined``; a failed exchange
     is ``exchange_failed``;
  2. the ``gmail.modify`` scope is among those granted (``scope_not_granted``), a refresh token
     came back (``no_refresh_token``) and fits Credential Manager (``token_too_long``);
  3. ``gmail.get_profile(access_token)``; an ``AdapterError`` is ``profile_failed``;
  4. ``identity_problem(requested, profile, env, allowlist)``: ``different_account``,
     ``brand_domain``, ``role_address``, ``invalid_address`` or ``not_allowlisted``;
  5. a flow superseded meanwhile revokes its grant and records nothing;
  6. the refresh token is written to ``gmail/<address>`` (``store_failed`` when it can't be);
  7. ``sending.connect_inbox(address, label, now=...)`` in a unit of work of its own. When it
     refuses (``ConnectRefused``: that failure) or fails (``store_failed``), the keyring entry
     is deleted and the new grant revoked;
  8. a refresh token it replaced is overwritten, not revoked (it is the same account and
     client, so Google's revoke could end the new grant too); the new access token is cached
     for the inbox.

  Every outcome is logged (``inbox_connect_failed`` with ``reason`` and ``code``, or
  ``inbox_connected`` with ``inbox_id``), never the address, the label, a code, a verifier or a
  token. A crash in the job is ``crashed``; one in steps 2 to 4 (any exception, not only an
  ``AdapterError``) also revokes the new grant (``inbox_connect_crashed``). No ``repr`` here
  shows an address or a label.

- ``access_token(inbox_id) -> str``: the inbox's access token, cached in memory until 60 s
  before it expires, else refreshed with ``gmail/<address>``. It must be called outside any
  transaction (``RuntimeError`` otherwise). No refresh token is ``AdapterError``
  ``gmail_not_connected``; an unknown inbox ``gmail_unknown_inbox``; no client
  ``google_client_not_imported`` (none retryable). Google's ``google_invalid_grant`` runs
  ``sending.mark_auth_failed(inbox_id)`` (in a unit of work of its own: ``active`` or
  ``paused`` to ``auth_failed``, one event, one IDs-only alert; if that fails it is logged as
  ``inbox_mark_auth_failed_failed`` with the inbox id) and raises ``AdapterError``
  ``gmail_auth_failed``; every other ``AdapterError`` propagates. ``forget_token(inbox_id)``
  drops the cached token (a Gmail 401 or 403, Story 6.8 on).
- ``check_grant(address, *, network) -> GrantProblem | None`` (doctor's
  ``sending_inbox_grants``): without ``network``, whether ``gmail/<address>`` is stored; with
  it, also a refresh and a ``getProfile`` that must return the same address. It reads no
  database, writes nothing and changes no status.
"""

import contextvars
import functools
import hmac
import logging
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from secrets import token_urlsafe
from typing import Any, Final, Protocol

from nextron.app.queries import sending as sending_queries
from nextron.app.queries import setup as setup_queries
from nextron.db.uow import in_transaction
from nextron.domain.google_oauth import (
    CLIENT_SECRET_NAME,
    CONSENT_TIMEOUT,
    ClientConfig,
    authorization_url_for_scope,
    code_challenge,
    decode_client_config,
    has_scope,
    same_account,
)
from nextron.domain.sending_inbox import (
    GMAIL_SCOPE,
    ConnectFailure,
    ConnectRefused,
    InboxRefused,
    InboxStatus,
    PauseCause,
    allowlist_refusal,
    check_address,
    check_label,
    connect_failure,
    gmail_secret_name,
    identity_problem,
)
from nextron.domain.settings_schema import InvalidSettingValue, Key, SettingNotSet
from nextron.domain.sink import sink_applies
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.gmail import GmailPort
from nextron.ports.google_oauth import (
    GOOGLE_CLIENT_NOT_IMPORTED,
    GOOGLE_INVALID_GRANT,
    ConsentListener,
    GoogleOAuthPort,
    Query,
    TokenSet,
)
from nextron.ports.secrets import MAX_SECRET_LENGTH, SecretsPort, is_secret_name, secret_length

CONSENT_WAIT_S: Final = CONSENT_TIMEOUT.total_seconds()
TOKEN_MARGIN: Final = timedelta(seconds=60)
GMAIL_NOT_CONNECTED: Final = "gmail_not_connected"
GMAIL_UNKNOWN_INBOX: Final = "gmail_unknown_inbox"
GMAIL_AUTH_FAILED: Final = "gmail_auth_failed"

INBOX_CONSENT_STARTED: Final = "inbox_consent_started"
INBOX_CONNECT_FAILED: Final = "inbox_connect_failed"
INBOX_CONNECTED: Final = "inbox_connected"
INBOX_TOKEN_REVOKED: Final = "inbox_token_revoked"
INBOX_REVOKE_FAILED: Final = "inbox_revoke_failed"
INBOX_CONNECT_CRASHED: Final = "inbox_connect_crashed"
INBOX_GRANT_INVALID: Final = "inbox_grant_invalid"
INBOX_MARK_AUTH_FAILED_FAILED: Final = "inbox_mark_auth_failed_failed"

Browser = Callable[[str], bool]
Listen = Callable[[], ConsentListener]
Start = Callable[[Callable[[], None]], None]
OpenUnit = Callable[..., AbstractContextManager[Any]]

log = logging.getLogger(__name__)


def start_daemon(job: Callable[[], None]) -> None:
    """Run ``job`` on a new daemon thread."""
    threading.Thread(target=job, name="inbox-consent", daemon=True).start()


class Connector(Protocol):
    """What ``InboxConnections`` needs of the ``Sending`` service."""

    def connect_inbox(self, address: str, label: str | None, *, now: datetime) -> Any: ...

    def mark_auth_failed(self, inbox_id: str) -> bool: ...


class Revocation(StrEnum):
    """What happened to a token that was let go."""

    REVOKED = "revoked"
    FAILED = "failed"  # Google failed; revoke it by hand at myaccount.google.com


class GrantProblem(StrEnum):
    """What doctor found wrong with a connected inbox's grant."""

    NOT_STORED = "not_stored"
    KEYRING_FAILED = "keyring_failed"
    CLIENT_NOT_IMPORTED = "client_not_imported"
    INVALID_GRANT = "invalid_grant"
    UNREACHABLE = "unreachable"
    REFRESH_FAILED = "refresh_failed"
    PROFILE_FAILED = "profile_failed"
    DIFFERENT_ACCOUNT = "different_account"


@dataclass(frozen=True, slots=True)
class ConnectOutcome:
    """How the last flow ended: ``failure`` is ``None`` when the inbox was connected
    (``inbox_id``; ``paused`` when its domain's newest check paused it at once). ``revocation``
    is what happened to the new grant after a failure (``None`` after a success); ``code`` the
    error code behind a failure. Its ``repr`` leaves the address out."""

    address: str = field(repr=False)
    failure: ConnectFailure | None
    inbox_id: str | None = None
    paused: PauseCause | None = None
    revocation: Revocation | None = None
    code: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectStatus:
    """The panel's view: the waiting flow's address and deadline (``None`` when none waits),
    and the last flow's outcome. Its ``repr`` leaves the address out."""

    waiting_for: str | None = field(repr=False)
    waiting_until: datetime | None
    outcome: ConnectOutcome | None


@dataclass(frozen=True, slots=True)
class _Plan:
    address: str = field(repr=False)
    label: str | None = field(repr=False)
    client: ClientConfig = field(repr=False)


@dataclass(eq=False)
class _Flow:
    """One waiting consent flow; it never leaves this module."""

    address: str = field(repr=False)
    label: str | None = field(repr=False)
    client: ClientConfig = field(repr=False)
    listener: ConsentListener
    state: str = field(repr=False)
    verifier: str = field(repr=False)
    redirect_uri: str
    waiting_until: datetime


@dataclass(frozen=True, slots=True)
class _CachedToken:
    token: str = field(repr=False)
    expires_at: datetime


def _new_token(tokens: TokenSet) -> str:
    """The token that revokes a new grant: the refresh token, else the access token."""
    return tokens.refresh_token if tokens.refresh_token is not None else tokens.access_token


def _isolated[R](job: Callable[[], R]) -> R:
    """Run ``job`` in a fresh ``contextvars.Context``, so its unit of work is its own."""
    return contextvars.Context().run(job)


class InboxConnections:
    """Connects Sending Inboxes through Gmail OAuth; see the module docstring."""

    def __init__(
        self,
        secrets: SecretsPort,
        clock: Clock,
        *,
        oauth: GoogleOAuthPort,
        listen: Listen,
        browser: Browser,
        gmail: GmailPort,
        env: str,
        sending: Connector,
        open_unit: OpenUnit,
        start: Start = start_daemon,
    ) -> None:
        self._secrets = secrets
        self._clock = clock
        self._oauth = oauth
        self._listen = listen
        self._browser = browser
        self._gmail = gmail
        self._env = env
        self._sending = sending
        self._open_unit = open_unit
        self._start = start
        self._state_lock = threading.Lock()  # the flow, the outcome, cached tokens; never I/O
        self._store_lock = threading.Lock()  # keyring changes to inbox grants
        self._flow: _Flow | None = None
        self._outcome: ConnectOutcome | None = None
        self._tokens: dict[str, _CachedToken] = {}

    # Status ---------------------------------------------------------------------------------

    def status(self) -> ConnectStatus:
        """The waiting flow, if any, and the last flow's outcome."""
        with self._state_lock:
            flow, outcome = self._flow, self._outcome
        return ConnectStatus(
            flow.address if flow is not None else None,
            flow.waiting_until if flow is not None else None,
            outcome,
        )

    # Starting a flow ------------------------------------------------------------------------

    def start_connect(self, address: str, label: str) -> ConnectStatus:
        """The panel's Connect inbox; see the module docstring."""
        plan = _isolated(functools.partial(self._plan_connect, address, label))
        return self._begin(plan)

    def start_reconnect(self, inbox_id: str) -> ConnectStatus:
        """A registered inbox's Connect or Reconnect; see the module docstring."""
        plan = _isolated(functools.partial(self._plan_reconnect, inbox_id))
        return self._begin(plan)

    def _plan_connect(self, address: str, label: str) -> _Plan:
        try:
            checked = check_address(address)
            shown_label = check_label(label)
        except InboxRefused as refused:
            raise ConnectRefused(connect_failure(refused.refusal)) from None
        with self._open_unit(read_only=True):
            self._check_gate()
            self._check_inbox(checked.address)
        return _Plan(checked.address, shown_label, self._read_client())

    def _plan_reconnect(self, inbox_id: object) -> _Plan:
        with self._open_unit(read_only=True):
            self._check_gate()
            found = next(
                (view for view in sending_queries.inboxes() if view.id == inbox_id), None
            )
            if found is None:
                raise ConnectRefused(ConnectFailure.UNKNOWN_INBOX)
            self._check_inbox(found.address)
        return _Plan(found.address, None, self._read_client())

    @staticmethod
    def _check_gate() -> None:
        try:
            confirmed = setup_queries.get(Key.GMAIL_INTERNAL_APP_CONFIRMED) is True
        except (SettingNotSet, InvalidSettingValue):
            confirmed = False
        if not confirmed:
            raise ConnectRefused(ConnectFailure.GATE_OFF)

    def _check_inbox(self, address: str) -> None:
        """Steps 2 to 5 for ``address``, in the open read-only unit of work."""
        if not is_secret_name(gmail_secret_name(address)):
            raise ConnectRefused(ConnectFailure.INVALID_ADDRESS)
        refusal = allowlist_refusal(address, self._env, self._allowlist())
        if refusal is not None:
            raise ConnectRefused(connect_failure(refusal))
        try:
            setup_queries.get(Key.SENDER_NAME)
        except (SettingNotSet, InvalidSettingValue):
            raise ConnectRefused(ConnectFailure.SENDER_NAME_UNSET) from None
        for view in sending_queries.inboxes():
            if view.address == address:
                if view.status in (InboxStatus.ACTIVE, InboxStatus.PAUSED):
                    raise ConnectRefused(ConnectFailure.ALREADY_CONNECTED)
                return
        if sending_queries.address_registered(address):  # held only by a removed inbox
            raise ConnectRefused(ConnectFailure.INBOX_REMOVED)

    def _allowlist(self) -> tuple[object, ...]:
        """The ``sink_allowlist`` outside ``prod``; unreadable counts as empty (refused)."""
        if not sink_applies(self._env):
            return ()
        try:
            return tuple(setup_queries.get(Key.SINK_ALLOWLIST))
        except (SettingNotSet, InvalidSettingValue):
            return ()

    def _read_client(self) -> ClientConfig:
        try:
            client = decode_client_config(self._secrets.read(CLIENT_SECRET_NAME))
        except AdapterError:
            raise ConnectRefused(ConnectFailure.STORE_FAILED) from None
        if client is None:
            raise ConnectRefused(ConnectFailure.CLIENT_NOT_IMPORTED)
        return client

    def _begin(self, plan: _Plan) -> ConnectStatus:
        self._cancel()
        try:
            listener = self._listen()
        except Exception as failed:  # noqa: BLE001 - any failure to listen is listener_failed
            code = failed.code if isinstance(failed, AdapterError) else None
            self._record(ConnectOutcome(plan.address, ConnectFailure.LISTENER_FAILED, code=code))
            return self.status()
        try:
            flow = _Flow(
                address=plan.address,
                label=plan.label,
                client=plan.client,
                listener=listener,
                state=token_urlsafe(32),
                verifier=token_urlsafe(64),
                redirect_uri=listener.redirect_uri(),
                waiting_until=self._clock.now() + CONSENT_TIMEOUT,
            )
        except Exception:  # noqa: BLE001 - the listener must not stay open
            listener.close()
            self._record(ConnectOutcome(plan.address, ConnectFailure.LISTENER_FAILED))
            return self.status()
        with self._state_lock:
            overwritten, self._flow = self._flow, flow
            self._outcome = None
        if overwritten is not None:  # a concurrent click registered one since _cancel
            overwritten.listener.close()
        url = authorization_url_for_scope(
            plan.client.client_id,
            GMAIL_SCOPE,
            flow.redirect_uri,
            flow.state,
            code_challenge(flow.verifier),
        )
        try:
            opened = bool(self._browser(url))
        except Exception:  # noqa: BLE001 - any browser failure is browser_failed
            opened = False
        if not opened:
            self._abandon(flow, ConnectFailure.BROWSER_FAILED)
            return self.status()
        log.info(INBOX_CONSENT_STARTED)
        try:
            self._start(functools.partial(self._run, flow))
        except Exception:  # noqa: BLE001 - no job, no flow
            log.error(INBOX_CONNECT_CRASHED)
            self._abandon(flow, ConnectFailure.CRASHED)
        return self.status()

    def _record(self, outcome: ConnectOutcome) -> None:
        """Record ``outcome`` with no waiting flow."""
        with self._state_lock:
            self._outcome = outcome
        _log_outcome(outcome)

    def _cancel(self) -> None:
        """Drop the waiting flow and close its listener: it records no outcome."""
        with self._state_lock:
            flow, self._flow = self._flow, None
        if flow is not None:
            flow.listener.close()

    def _abandon(self, flow: _Flow, failure: ConnectFailure) -> None:
        try:
            flow.listener.close()
        finally:
            self._finish(flow, ConnectOutcome(flow.address, failure))

    def _finish(self, flow: _Flow, outcome: ConnectOutcome) -> None:
        """Record ``outcome`` unless ``flow`` was superseded meanwhile."""
        with self._state_lock:
            if self._flow is not flow:
                return
            self._flow = None
            self._outcome = outcome
        _log_outcome(outcome)

    def _current(self, flow: _Flow) -> bool:
        with self._state_lock:
            return self._flow is flow

    # Completing a flow ----------------------------------------------------------------------

    def _run(self, flow: _Flow) -> None:
        """The flow's job: wait for Google's answer, close, complete."""
        try:
            try:
                query = flow.listener.wait(CONSENT_WAIT_S)
            finally:
                flow.listener.close()
            outcome = _isolated(functools.partial(self._complete, flow, query))
        except Exception:  # noqa: BLE001 - completing never raises
            log.error(INBOX_CONNECT_CRASHED)
            outcome = ConnectOutcome(flow.address, ConnectFailure.CRASHED)
        if outcome is not None:
            self._finish(flow, outcome)

    def _complete(self, flow: _Flow, query: Query | None) -> ConnectOutcome | None:
        address = flow.address
        if query is None:
            return ConnectOutcome(address, ConnectFailure.TIMED_OUT)
        states = query.get("state", ())
        if len(states) != 1 or not hmac.compare_digest(
            states[0].encode("utf-8", "replace"), flow.state.encode("utf-8")
        ):
            return ConnectOutcome(address, ConnectFailure.STATE_MISMATCH)
        codes = query.get("code", ())
        if "error" in query or len(codes) != 1 or not codes[0]:
            return ConnectOutcome(address, ConnectFailure.DECLINED)
        try:
            tokens = self._oauth.exchange(flow.client, codes[0], flow.verifier, flow.redirect_uri)
        except AdapterError as failed:
            return ConnectOutcome(address, ConnectFailure.EXCHANGE_FAILED, code=failed.code)
        try:
            failure, code = self._verify(flow, tokens)
        except Exception:  # noqa: BLE001 - an unexpected failure still lets the grant go
            log.error(INBOX_CONNECT_CRASHED)
            revocation = self._revoke(_new_token(tokens))
            return ConnectOutcome(address, ConnectFailure.CRASHED, revocation=revocation)
        if failure is not None:
            revocation = self._revoke(_new_token(tokens))
            return ConnectOutcome(address, failure, revocation=revocation, code=code)
        if not self._current(flow):  # superseded: let the grant go, record nothing
            self._revoke(_new_token(tokens))
            return None
        return self._store(flow, tokens)

    def _verify(
        self, flow: _Flow, tokens: TokenSet
    ) -> tuple[ConnectFailure | None, str | None]:
        """Steps 2 to 4: the failure and its code, or ``(None, None)``."""
        if not has_scope(tokens.scope, GMAIL_SCOPE):
            return ConnectFailure.SCOPE_NOT_GRANTED, None
        if tokens.refresh_token is None:
            return ConnectFailure.NO_REFRESH_TOKEN, None
        if secret_length(tokens.refresh_token) > MAX_SECRET_LENGTH:
            return ConnectFailure.TOKEN_TOO_LONG, None
        try:
            profile = self._gmail.get_profile(tokens.access_token)
        except AdapterError as failed:
            return ConnectFailure.PROFILE_FAILED, failed.code
        with self._open_unit(read_only=True):
            allowlist = self._allowlist()
        return identity_problem(flow.address, profile, self._env, allowlist), None

    def _store(self, flow: _Flow, tokens: TokenSet) -> ConnectOutcome:
        """Steps 6 to 8."""
        address = flow.address
        name = gmail_secret_name(address)
        refresh_token = _new_token(tokens)  # _verify checked it is the refresh token
        with self._store_lock:
            try:
                self._secrets.write(name, refresh_token)
            except AdapterError as failed:
                return ConnectOutcome(
                    address,
                    ConnectFailure.STORE_FAILED,
                    revocation=self._revoke(refresh_token),
                    code=failed.code,
                )
            now = self._clock.now()
            try:
                with self._open_unit():
                    connected = self._sending.connect_inbox(address, flow.label, now=now)
            except Exception as failed:  # noqa: BLE001 - nothing is kept: delete and revoke
                failure = (
                    failed.failure
                    if isinstance(failed, ConnectRefused)
                    else ConnectFailure.STORE_FAILED
                )
                self._delete(name)
                stored = failure is ConnectFailure.STORE_FAILED
                return ConnectOutcome(
                    address,
                    failure,
                    revocation=self._revoke(refresh_token),
                    code=type(failed).__name__.lower() if stored else None,
                )
            # A replaced refresh token is not revoked: it is the same account and client (the
            # identity check), and Google's revoke could end the new grant with it.
            with self._state_lock:
                self._tokens[connected.inbox_id] = _CachedToken(
                    tokens.access_token, now + timedelta(seconds=tokens.expires_in)
                )
        return ConnectOutcome(address, None, connected.inbox_id, connected.paused)

    def _delete(self, name: str) -> None:
        try:
            self._secrets.delete(name)
        except AdapterError as failed:
            log.warning(INBOX_REVOKE_FAILED, extra={"reason": "delete", "code": failed.code})

    def _revoke(self, token: str) -> Revocation:
        try:
            self._oauth.revoke(token)
        except AdapterError as failed:
            log.warning(INBOX_REVOKE_FAILED, extra={"reason": "revoke", "code": failed.code})
            return Revocation.FAILED
        log.info(INBOX_TOKEN_REVOKED)
        return Revocation.REVOKED

    # Access tokens --------------------------------------------------------------------------

    def access_token(self, inbox_id: str) -> str:
        """A valid access token for the inbox; see the module docstring."""
        if in_transaction():
            raise RuntimeError("access_token calls Google; call it outside any transaction")
        now = self._clock.now()
        with self._state_lock:
            cached = self._tokens.get(inbox_id)
        if cached is not None and cached.expires_at - TOKEN_MARGIN > now:
            return cached.token
        address = _isolated(functools.partial(self._address_of, inbox_id))
        if address is None:
            raise AdapterError(False, GMAIL_UNKNOWN_INBOX, "no Sending Inbox has that id")
        client, token = self._stored(address)
        try:
            tokens = self._oauth.refresh(client, token)
        except AdapterError as failed:
            if failed.code != GOOGLE_INVALID_GRANT:
                raise
            self.forget_token(inbox_id)
            log.warning(INBOX_GRANT_INVALID, extra={"inbox_id": inbox_id})
            try:
                _isolated(functools.partial(self._mark_auth_failed, inbox_id))
            except Exception:  # noqa: BLE001 - the caller still learns the grant is dead
                log.error(INBOX_MARK_AUTH_FAILED_FAILED, extra={"inbox_id": inbox_id})
            raise AdapterError(
                False, GMAIL_AUTH_FAILED, "Google refused the inbox's grant; reconnect it"
            ) from None
        with self._state_lock:
            self._tokens[inbox_id] = _CachedToken(
                tokens.access_token, now + timedelta(seconds=tokens.expires_in)
            )
        return tokens.access_token

    def forget_token(self, inbox_id: str) -> None:
        """Drop the inbox's cached access token."""
        with self._state_lock:
            self._tokens.pop(inbox_id, None)

    def _address_of(self, inbox_id: object) -> str | None:
        with self._open_unit(read_only=True):
            return next(
                (view.address for view in sending_queries.inboxes() if view.id == inbox_id), None
            )

    def _mark_auth_failed(self, inbox_id: str) -> None:
        with self._open_unit():
            self._sending.mark_auth_failed(inbox_id)

    def _stored(self, address: str) -> tuple[ClientConfig, str]:
        """The client and the inbox's refresh token; ``AdapterError`` when either is missing
        (a Credential Manager failure propagates)."""
        client = decode_client_config(self._secrets.read(CLIENT_SECRET_NAME))
        if client is None:
            raise AdapterError(
                False, GOOGLE_CLIENT_NOT_IMPORTED, "the Google Desktop client is not imported"
            )
        token = self._secrets.read(gmail_secret_name(address))
        if token is None:
            raise AdapterError(False, GMAIL_NOT_CONNECTED, "the inbox has no stored grant")
        return client, token

    # Doctor ---------------------------------------------------------------------------------

    def check_grant(self, address: str, *, network: bool) -> GrantProblem | None:
        """What is wrong with the grant of the inbox ``address``, or ``None``; see the module
        docstring."""
        try:
            client, token = self._stored(address)
        except AdapterError as failed:
            if failed.code == GMAIL_NOT_CONNECTED:
                return GrantProblem.NOT_STORED
            if failed.code == GOOGLE_CLIENT_NOT_IMPORTED:
                return GrantProblem.CLIENT_NOT_IMPORTED
            return GrantProblem.KEYRING_FAILED
        if not network:
            return None
        try:
            tokens = self._oauth.refresh(client, token)
        except AdapterError as failed:
            if failed.code == GOOGLE_INVALID_GRANT:
                return GrantProblem.INVALID_GRANT
            return GrantProblem.UNREACHABLE if failed.retryable else GrantProblem.REFRESH_FAILED
        try:
            profile = self._gmail.get_profile(tokens.access_token)
        except AdapterError as failed:
            return GrantProblem.UNREACHABLE if failed.retryable else GrantProblem.PROFILE_FAILED
        if not same_account(profile, address):
            return GrantProblem.DIFFERENT_ACCOUNT
        return None


def _log_outcome(outcome: ConnectOutcome) -> None:
    if outcome.failure is None:
        log.info(INBOX_CONNECTED, extra={"inbox_id": outcome.inbox_id})
        return
    extra: dict[str, str] = {"reason": str(outcome.failure)}
    if outcome.code is not None:
        extra["code"] = outcome.code
    if outcome.revocation is not None:
        extra["revocation"] = str(outcome.revocation)
    log.warning(INBOX_CONNECT_FAILED, extra=extra)
