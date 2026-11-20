"""Google OAuth for an installed app (AD-12, AD-14, INT-2, INT-3, FR-56, NFR-18, UX-DR27).

The engine holds Google grants through the installed-app ("Desktop app") loopback flow with
PKCE. Mac imports the Desktop client's downloaded ``client_secret.json`` once; each grant is
then started by a POST on the Credentials page and finished on a one-shot listener on
``127.0.0.1`` (``nextron.adapters.google_oauth``, run by ``nextron.app.setup.api.GoogleAccess``).
This module holds the pure rules; it takes ``now`` and does no I/O.

**Endpoints and limits.** ``AUTH_URI``, ``TOKEN_URI`` and ``REVOKE_URI`` are Google's;
``ID_TOKEN_ISSUERS`` are the two ``iss`` values Google uses. A flow waits at most
``CONSENT_TIMEOUT`` (5 minutes). A client file holds at most ``MAX_CLIENT_FILE_BYTES``.
``internal_notify`` needs an account on ``INTERNAL_DOMAIN``. Every flow also asks for
``IDENTITY_SCOPES`` (``openid email``), because neither ``gmail.send`` nor
``postmaster.readonly`` can read the account's address: the address comes from the ID token.

**Keyring names.** The client lives at ``CLIENT_SECRET_NAME`` (``google/client_config``) as
``encode_client_config``'s compact JSON, with its import time (``encode_updated_at``) at
``CLIENT_META_NAME``. A grant's refresh token lives at ``GrantSpec.secret_name``
(``google/<grant>``) and its ``GrantMeta`` at ``GrantSpec.meta_name`` (``meta/google/<grant>``).

**Grants.** ``GRANTS``, in page order:

- ``internal_notify``, "Internal notifications (internal_notify)":
  ``https://www.googleapis.com/auth/gmail.send``;
- ``postmaster``, "Postmaster Tools (postmaster)":
  ``https://www.googleapis.com/auth/postmaster.readonly``.

``grant_spec(value)`` returns one, and raises ``UnknownGrant`` (``unknown_grant``) otherwise.
``ClientNotImported`` (``google_client_not_imported``) is raised when a grant is asked for
before a client is stored.

**The client file.**

- ``client_path(text)`` cleans what Mac typed: spaces at either end, then one pair of
  surrounding ``"`` (File Explorer's "Copy as path"), then spaces again. It refuses
  (``InvalidClientFile``) an empty result, one over ``MAX_PATH_LENGTH`` characters, one with a
  control character, anything but an absolute path on a single-letter drive (so UNC,
  ``\\\\?\\`` and relative paths are refused), a colon after the drive (an NTFS alternate data
  stream such as ``file.txt:s.json``), and a name that doesn't end in ``.json`` (any case). It
  returns the path as Windows writes it.
- ``parse_client_file(raw)`` reads the bytes as UTF-8 (a BOM is allowed) JSON: a top-level
  object with an ``"installed"`` object (a ``"web"`` client gets its own reason), whose
  ``client_id`` fully matches ``CLIENT_ID_PATTERN`` and whose ``client_secret`` fully matches
  ``CLIENT_SECRET_PATTERN``. Only those two values are kept, as a ``ClientConfig`` whose
  ``repr`` leaves the secret out.
- ``encode_client_config`` / ``decode_client_config`` write and read the keyring text; decoding
  returns ``None`` for anything that breaks the same field rules.

``InvalidClientFile.reason`` says what a valid file or path is, never what the file held or
what the path was.

**The consent request.** ``code_challenge(verifier)`` is the PKCE S256 challenge (RFC 7636).
``authorization_url`` builds the consent URL with exactly these parameters, in order:
``response_type=code``, ``client_id``, ``redirect_uri``, ``scope`` (the grant's scope, then
``openid email``), ``state``, ``code_challenge``, ``code_challenge_method=S256``,
``access_type=offline`` and ``prompt=select_account consent`` (so every grant returns a refresh
token and Mac picks the account). There is no ``include_granted_scopes``.
``authorization_url_for_scope(client_id, scope, redirect_uri, state, challenge)`` (Story 6.2)
has the same parameters in the same order, with ``scope`` exactly the one given (no
``openid email``): a Sending Inbox asks for ``gmail.modify`` only and reads its address from
``users.getProfile``.

**The answer.**

- ``id_token_account(id_token, client_id, now)`` is the ID token's ``email``, or ``None``. The
  ID token arrives straight from Google's token endpoint over TLS (OIDC Core 3.1.3.7), so its
  signature isn't checked; its claims are: ``iss`` in ``ID_TOKEN_ISSUERS``, ``aud`` a ``str``
  equal to ``client_id``, ``exp`` after ``now``, ``email_verified`` exactly ``true``, and an
  ``email`` that ``nextron.domain.sink.is_sink_recipient`` accepts and that holds an ``@``.
- ``scope_granted(scope_text, spec)``: whether the grant's scope is among the space-separated
  scopes Google returned (under granular consent Mac can untick it); ``has_scope(scope_text,
  scope)`` is the same test for any one scope.
- ``account_problem(spec, account, env, allowlist)``: ``not_internal_domain`` when an
  ``internal_notify`` account's part after its last ``@``, ASCII-lowercased, isn't exactly
  ``nextronai.com`` (no subdomains); ``not_allowlisted`` when the sink applies to ``env``
  (``sink_applies``) and the account isn't on ``allowlist`` (``allowlisted``); else ``None``.
- ``ConsentFailure`` names every way a flow can end without a grant.

**Grant metadata.** ``GrantMeta(account, granted_at, needs_reconsent_at=None)`` is encoded as
compact JSON, ``{"account": ..., "granted_at": "...Z"}`` plus ``"needs_reconsent_at"`` only when
it is set, with ``encode_updated_at`` times. ``decode_grant_meta`` returns ``None`` for anything
else. ``same_account(a, b)`` compares two addresses after ASCII lowercasing.

Import rule: the standard library and ``nextron.domain``.
"""

import base64
import binascii
import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import PureWindowsPath
from typing import Final
from urllib.parse import quote, urlencode

from nextron.domain.credentials import (
    KEY_PATTERN,
    META_PREFIX,
    decode_updated_at,
    encode_updated_at,
)
from nextron.domain.errors import DomainError
from nextron.domain.sink import allowlisted, is_sink_recipient, sink_applies

AUTH_URI: Final = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI: Final = "https://oauth2.googleapis.com/token"
REVOKE_URI: Final = "https://oauth2.googleapis.com/revoke"
ID_TOKEN_ISSUERS: Final = frozenset({"https://accounts.google.com", "accounts.google.com"})

CONSENT_TIMEOUT: Final = timedelta(minutes=5)
MAX_CLIENT_FILE_BYTES: Final = 65_536
MAX_PATH_LENGTH: Final = 1_024
INTERNAL_DOMAIN: Final = "nextronai.com"
IDENTITY_SCOPES: Final = ("openid", "email")

CLIENT_SECRET_NAME: Final = "google/client_config"
CLIENT_META_NAME: Final = META_PREFIX + CLIENT_SECRET_NAME
GRANT_PREFIX: Final = "google/"

CLIENT_ID_PATTERN: Final = re.compile(
    r"[0-9]{1,30}-[0-9a-z]{1,64}\.apps\.googleusercontent\.com", re.ASCII
)  # matched with fullmatch
CLIENT_SECRET_PATTERN: Final = re.compile(r"[\x21-\x7e]{1,200}")  # matched with fullmatch
_DRIVE: Final = re.compile(r"[A-Za-z]:")  # matched with fullmatch
_BASE64URL: Final = re.compile(r"[A-Za-z0-9_-]*")  # matched with fullmatch
_ASCII_LOWER: Final = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
)
_META_FIELDS: Final = frozenset({"account", "granted_at"})

# Why a client path or file was refused: what a valid one is, never what was given.
PATH_EMPTY: Final = "enter the full path of the downloaded client_secret.json file"
PATH_TOO_LONG: Final = f"the path has more than {MAX_PATH_LENGTH:,} characters"
PATH_CONTROL: Final = "the path contains a control character"
PATH_NOT_LOCAL: Final = (
    "enter a full path on a drive of this PC, starting with a drive letter such as C:\\ "
    "(network, \\\\?\\ and relative paths are refused)"
)
PATH_NOT_JSON: Final = "the file name must end in .json"
PATH_COLON: Final = "the path may not contain a colon after the drive letter"
FILE_MISSING: Final = "there is no file at that path"
FILE_NOT_REGULAR: Final = "the path is not a regular file (a folder, link or shortcut target)"
FILE_TOO_LARGE: Final = f"the file is larger than {MAX_CLIENT_FILE_BYTES // 1024} KiB"
FILE_UNREADABLE: Final = "the file could not be read"
NOT_JSON: Final = "the file is not UTF-8 JSON"
NOT_INSTALLED: Final = "the file is not a Google Desktop app client (it has no installed client)"
WEB_CLIENT: Final = (
    "the file is a Web application client; in Google Cloud Console, create a Desktop app "
    "client and download its JSON"
)
BAD_CLIENT_ID: Final = "the client_id is not a Google client ID"
BAD_CLIENT_SECRET: Final = "the client_secret is missing or malformed"

INTERNAL_NOTIFY_DOMAIN: Final = "internal_notify must be a nextronai.com account"


class GoogleGrant(StrEnum):
    """Every Google grant the engine holds, in page order."""

    INTERNAL_NOTIFY = "internal_notify"
    POSTMASTER = "postmaster"


@dataclass(frozen=True, slots=True)
class GrantSpec:
    """One grant: its key, what the Credentials page shows, and the scope it asks for."""

    grant: GoogleGrant
    label: str
    scope: str

    @property
    def secret_name(self) -> str:
        """The keyring entry that holds the grant's refresh token."""
        return GRANT_PREFIX + self.grant

    @property
    def meta_name(self) -> str:
        """The keyring entry that holds the grant's ``GrantMeta``, never a token."""
        return META_PREFIX + self.secret_name


GRANTS: Final[tuple[GrantSpec, ...]] = (
    GrantSpec(
        GoogleGrant.INTERNAL_NOTIFY,
        "Internal notifications (internal_notify)",
        "https://www.googleapis.com/auth/gmail.send",
    ),
    GrantSpec(
        GoogleGrant.POSTMASTER,
        "Postmaster Tools (postmaster)",
        "https://www.googleapis.com/auth/postmaster.readonly",
    ),
)


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """The Desktop client's id and secret; the secret is left out of ``repr``."""

    client_id: str
    client_secret: str = field(repr=False)


class ConsentFailure(StrEnum):
    """Why a consent flow ended without a stored grant."""

    TIMED_OUT = "timed_out"
    STATE_MISMATCH = "state_mismatch"
    DECLINED = "declined"
    EXCHANGE_FAILED = "exchange_failed"
    NO_ACCOUNT = "no_account"
    SCOPE_NOT_GRANTED = "scope_not_granted"
    NO_REFRESH_TOKEN = "no_refresh_token"
    NOT_INTERNAL_DOMAIN = "not_internal_domain"
    NOT_ALLOWLISTED = "not_allowlisted"
    STORE_FAILED = "store_failed"
    BROWSER_FAILED = "browser_failed"
    LISTENER_FAILED = "listener_failed"
    CRASHED = "crashed"


@dataclass(frozen=True, slots=True)
class GrantMeta:
    """What ``meta/google/<grant>`` holds: the account, when it was granted, and when Google
    refused the stored token, if it did."""

    account: str | None
    granted_at: datetime
    needs_reconsent_at: datetime | None = None


# Errors ---------------------------------------------------------------------------------------


class UnknownGrant(DomainError):
    """A value that names no grant in ``GRANTS``."""

    code = "unknown_grant"

    def __init__(self, value: object) -> None:
        named = isinstance(value, str) and KEY_PATTERN.fullmatch(value) is not None
        super().__init__(f"unknown Google grant: {value if named else '(not a grant name)'}")
        self.grant = str(value) if named else None


class InvalidClientFile(DomainError):
    """A client path or file that is refused; ``reason`` never quotes the file or the path."""

    code = "invalid_client_file"

    def __init__(self, reason: str) -> None:
        super().__init__(f"invalid Google client file: {reason}")
        self.reason = reason


class ClientNotImported(DomainError):
    """A grant was asked for before a Desktop client was imported."""

    code = "google_client_not_imported"

    def __init__(self) -> None:
        super().__init__("the Google Desktop client is not imported")


# Grants ---------------------------------------------------------------------------------------


def grant_spec(value: object) -> GrantSpec:
    """The grant ``value`` names; raises ``UnknownGrant``."""
    for spec in GRANTS:
        if isinstance(value, str) and value == spec.grant:
            return spec
    raise UnknownGrant(value)


# The client file ------------------------------------------------------------------------------


def _has_control(text: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in text)


def client_path(text: object) -> str:
    """The cleaned, checked path of a client file; raises ``InvalidClientFile``."""
    if not isinstance(text, str):
        raise InvalidClientFile(PATH_EMPTY)
    cleaned = text.strip(" ")
    if len(cleaned) >= 2 and cleaned[0] == '"' and cleaned[-1] == '"':
        cleaned = cleaned[1:-1]
    cleaned = cleaned.strip(" ")
    if not cleaned:
        raise InvalidClientFile(PATH_EMPTY)
    if len(cleaned) > MAX_PATH_LENGTH:
        raise InvalidClientFile(PATH_TOO_LONG)
    if _has_control(cleaned):
        raise InvalidClientFile(PATH_CONTROL)
    path = PureWindowsPath(cleaned)
    if not (path.is_absolute() and _DRIVE.fullmatch(path.drive)):
        raise InvalidClientFile(PATH_NOT_LOCAL)
    if ":" in str(path)[len(path.drive) :]:  # an NTFS stream (file.txt:s.json), not a file
        raise InvalidClientFile(PATH_COLON)
    if not path.name.lower().endswith(".json"):
        raise InvalidClientFile(PATH_NOT_JSON)
    return str(path)


def _client_fields(client_id: object, client_secret: object) -> ClientConfig | None:
    if not (isinstance(client_id, str) and CLIENT_ID_PATTERN.fullmatch(client_id)):
        return None
    if not (isinstance(client_secret, str) and CLIENT_SECRET_PATTERN.fullmatch(client_secret)):
        return None
    return ClientConfig(client_id, client_secret)


def parse_client_file(raw: bytes) -> ClientConfig:
    """The Desktop client in a downloaded ``client_secret.json``; raises
    ``InvalidClientFile``."""
    try:
        document = json.loads(bytes(raw).decode("utf-8-sig"))
    except (TypeError, UnicodeDecodeError, ValueError, RecursionError):
        raise InvalidClientFile(NOT_JSON) from None
    if not isinstance(document, dict):
        raise InvalidClientFile(NOT_INSTALLED)
    installed = document.get("installed")
    if not isinstance(installed, dict):
        raise InvalidClientFile(WEB_CLIENT if "web" in document else NOT_INSTALLED)
    client_id, client_secret = installed.get("client_id"), installed.get("client_secret")
    if not (isinstance(client_id, str) and CLIENT_ID_PATTERN.fullmatch(client_id)):
        raise InvalidClientFile(BAD_CLIENT_ID)
    config = _client_fields(client_id, client_secret)
    if config is None:
        raise InvalidClientFile(BAD_CLIENT_SECRET)
    return config


def encode_client_config(config: ClientConfig) -> str:
    """The keyring text of ``config``: compact JSON with only the id and the secret."""
    return json.dumps(
        {"client_id": config.client_id, "client_secret": config.client_secret},
        separators=(",", ":"),
    )


def decode_client_config(text: object) -> ClientConfig | None:
    """The client ``text`` holds, or ``None`` when it isn't a valid ``encode_client_config``
    text."""
    if not isinstance(text, str):
        return None
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(document, dict) or set(document) != {"client_id", "client_secret"}:
        return None
    return _client_fields(document["client_id"], document["client_secret"])


# The consent request --------------------------------------------------------------------------


def code_challenge(verifier: str) -> str:
    """The PKCE S256 challenge of ``verifier``: its SHA-256, base64url without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(
    client_id: str, spec: GrantSpec, redirect_uri: str, state: str, challenge: str
) -> str:
    """The consent page URL for ``spec``; see the module docstring for its parameters."""
    return _consent_url(
        client_id, " ".join((spec.scope, *IDENTITY_SCOPES)), redirect_uri, state, challenge
    )


def authorization_url_for_scope(
    client_id: str, scope: str, redirect_uri: str, state: str, challenge: str
) -> str:
    """The consent page URL asking for exactly ``scope`` (no ``openid email``), with the same
    parameters as ``authorization_url`` otherwise (Story 6.2: a Sending Inbox's
    ``gmail.modify``)."""
    return _consent_url(client_id, scope, redirect_uri, state, challenge)


def _consent_url(
    client_id: str, scope_text: str, redirect_uri: str, state: str, challenge: str
) -> str:
    query = urlencode(
        [
            ("response_type", "code"),
            ("client_id", client_id),
            ("redirect_uri", redirect_uri),
            ("scope", scope_text),
            ("state", state),
            ("code_challenge", challenge),
            ("code_challenge_method", "S256"),
            ("access_type", "offline"),
            ("prompt", "select_account consent"),
        ],
        quote_via=quote,
    )
    return f"{AUTH_URI}?{query}"


# The answer -----------------------------------------------------------------------------------


def _claims(id_token: str) -> dict[str, object] | None:
    parts = id_token.split(".")
    if len(parts) != 3 or not _BASE64URL.fullmatch(parts[1]):
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, ValueError, RecursionError):
        return None
    return claims if isinstance(claims, dict) else None


def id_token_account(id_token: object, client_id: str, now: datetime) -> str | None:
    """The verified ``email`` of ``id_token`` for ``client_id`` at ``now``, or ``None``."""
    if not isinstance(id_token, str):
        return None
    claims = _claims(id_token)
    if claims is None:
        return None
    issuer, audience, expires = claims.get("iss"), claims.get("aud"), claims.get("exp")
    if not (isinstance(issuer, str) and issuer in ID_TOKEN_ISSUERS):
        return None
    if not (isinstance(audience, str) and audience == client_id):
        return None
    if isinstance(expires, bool) or not isinstance(expires, (int, float)):
        return None
    try:
        if not (math.isfinite(expires) and expires > now.timestamp()):
            return None
    except OverflowError:  # an integer too large for a float
        return None
    if claims.get("email_verified") is not True:
        return None
    email = claims.get("email")
    if not (is_sink_recipient(email) and "@" in email):
        return None
    return email


def scope_granted(scope_text: object, spec: GrantSpec) -> bool:
    """Whether ``spec.scope`` is among the space-separated scopes of ``scope_text``."""
    return has_scope(scope_text, spec.scope)


def has_scope(scope_text: object, scope: str) -> bool:
    """Whether ``scope`` is among the space-separated scopes of ``scope_text``."""
    return isinstance(scope_text, str) and scope in scope_text.split(" ")


def _ascii_lower(text: str) -> str:
    return text.translate(_ASCII_LOWER)


def account_problem(
    spec: GrantSpec, account: str, env: str, allowlist: Iterable[object]
) -> ConsentFailure | None:
    """Why ``account`` can't hold ``spec`` in ``env``, or ``None``; see the module
    docstring."""
    if spec.grant is GoogleGrant.INTERNAL_NOTIFY:
        if _ascii_lower(account.rpartition("@")[2]) != INTERNAL_DOMAIN:
            return ConsentFailure.NOT_INTERNAL_DOMAIN
    if sink_applies(env) and not allowlisted(account, allowlist):
        return ConsentFailure.NOT_ALLOWLISTED
    return None


# Grant metadata -------------------------------------------------------------------------------


def encode_grant_meta(meta: GrantMeta) -> str:
    """The keyring text of ``meta``: compact JSON with ``...Z`` times."""
    document: dict[str, object] = {
        "account": meta.account,
        "granted_at": encode_updated_at(meta.granted_at),
    }
    if meta.needs_reconsent_at is not None:
        document["needs_reconsent_at"] = encode_updated_at(meta.needs_reconsent_at)
    return json.dumps(document, separators=(",", ":"))


def decode_grant_meta(text: object) -> GrantMeta | None:
    """The ``GrantMeta`` ``text`` holds, or ``None`` when it isn't an ``encode_grant_meta``
    text."""
    if not isinstance(text, str):
        return None
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(document, dict):
        return None
    if set(document) not in (_META_FIELDS, _META_FIELDS | {"needs_reconsent_at"}):
        return None
    account = document["account"]
    if account is not None and not isinstance(account, str):
        return None
    granted_at = decode_updated_at(document["granted_at"])
    if granted_at is None:
        return None
    needs_reconsent_at = None
    if "needs_reconsent_at" in document:
        needs_reconsent_at = decode_updated_at(document["needs_reconsent_at"])
        if needs_reconsent_at is None:
            return None
    return GrantMeta(account, granted_at, needs_reconsent_at)


def same_account(a: object, b: object) -> bool:
    """Whether ``a`` and ``b`` are the same address after ASCII lowercasing."""
    return isinstance(a, str) and isinstance(b, str) and _ascii_lower(a) == _ascii_lower(b)
