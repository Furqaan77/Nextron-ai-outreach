"""External credentials, declared once with their keyring names (AD-12, FR-56, NFR-18, UX-DR27).

Every API key, token and passphrase the engine uses is declared here, in ``REGISTRY``, and
nowhere else. Each ``Credential`` has:

- ``key``: a ``CredentialKey`` member, the name the Credentials page and the adapters use;
- ``label``: what the Credentials page shows;
- ``secret_name``: the keyring entry that holds the value, under ``nextron-engine/<env>``;
- ``required``: whether the engine needs it (the doctor checks the required ones, Story 1.14);
- ``meta_name`` (a property): ``meta/<secret_name>``, the companion keyring entry that holds
  only the value's last-updated time, never the value.

The registry, in page order:

=====================  ==================================  ==========================  ========
Key                    Label                               Keyring name                Required
=====================  ==================================  ==========================  ========
``claude``             Claude API key                      ``anthropic/api_key``       yes
``google_places``      Google Places API key               ``google_places/api_key``   no
``hunter``             Hunter API key                      ``hunter/api_key``          yes
``prospeo``            Prospeo API key                     ``prospeo/api_key``         no
``companies_house``    Companies House API key             ``companies_house/api_key`` yes
``telegram_bot``       Telegram bot token                  ``telegram/bot_token``      yes
``unsubscribe_bearer`` Unsubscribe Endpoint bearer secret  ``unsub/bearer``            yes
``backup_passphrase``  Backup passphrase                   ``backup/passphrase``       no
``job_aggregator``     Job aggregator API key              ``job_aggregator/api_key``  no
=====================  ==================================  ==========================  ========

Other modules reach the registry only through ``credential(key)`` (``UnknownCredential`` for an
undeclared key) and ``declared()`` (every credential, in declaration order); both read
``REGISTRY`` when called, so a test can swap it with
``monkeypatch.setattr(credentials, "REGISTRY", probe)``.
``tests/contracts/test_credential_contracts.py`` checks every keyring name.

**The unsubscribe token keys** (Story 3.8, AD-15) are a per-kid family beside ``REGISTRY``,
not entries in it, because their keyring names depend on a kid (1 to 255,
``nextron.domain.unsub_token``). ``UnsubKey`` is ``k_tok`` (the AES-256-GCM-SIV key, which never
leaves the PC except in Mac's escrow copy) or ``k_edge`` (the HMAC key the unsubscribe Worker
also holds, as ``K_EDGE_<kid>``). ``UnsubKeyCredential(key, label, secret_prefix,
required=True)`` declares one; ``secret_name(kid)`` is ``<secret_prefix>/<kid>``
(``unsub/k_tok/<kid>``, ``unsub/k_edge/<kid>``) and ``meta_name(kid)`` its ``meta/`` entry, and
a kid outside 1 to 255 raises ``TypeError`` or ``ValueError``. ``required`` means "required for
the active kid": doctor's ``unsub_token_keys`` check enforces it, not ``required_credentials``.
Other modules reach the family only through ``unsub_key(key)`` (``UnknownCredential`` for
another key) and ``declared_unsub_keys()`` (``k_tok``, then ``k_edge``); both read
``UNSUB_KEYS`` when called. ``REGISTRY``, ``declared()`` and the ``required_credentials`` check
don't include them.

=============  =================================  ========================
Key            Label                              Keyring name
=============  =================================  ========================
``k_tok``      Token encryption key (k_tok)       ``unsub/k_tok/<kid>``
``k_edge``     Worker tag key (k_edge)            ``unsub/k_edge/<kid>``
=============  =================================  ========================

**Times.** ``encode_updated_at(at)`` is the text a ``meta/<name>`` entry holds:
``YYYY-MM-DDTHH:MM:SS.ffffffZ`` in UTC; a naive ``at`` raises ``ValueError``.
``decode_updated_at(text)`` reads it back as an aware UTC ``datetime``, and returns ``None`` for
anything else (no entry, another format, an impossible date).

**Errors.** ``UnknownCredential`` (``unknown_credential``) and ``InvalidCredentialValue``
(``invalid_credential``, with ``.key`` and ``.reason``; the message is ``"<key>: <reason>"``).
No message here ever includes a credential's value.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.errors import DomainError
from nextron.domain.unsub_token import check_kid

META_PREFIX: Final = "meta/"
KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,30}")  # matched with fullmatch
UPDATED_AT_PATTERN: Final = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", re.ASCII
)  # matched with fullmatch
UPDATED_AT_FORMAT: Final = "%Y-%m-%dT%H:%M:%S.%fZ"


class CredentialKey(StrEnum):
    """Every declared credential's key, in page order."""

    CLAUDE = "claude"
    GOOGLE_PLACES = "google_places"
    HUNTER = "hunter"
    PROSPEO = "prospeo"
    COMPANIES_HOUSE = "companies_house"
    TELEGRAM_BOT = "telegram_bot"
    UNSUBSCRIBE_BEARER = "unsubscribe_bearer"
    BACKUP_PASSPHRASE = "backup_passphrase"
    JOB_AGGREGATOR = "job_aggregator"


@dataclass(frozen=True, slots=True)
class Credential:
    """One declared credential; see the module docstring."""

    key: CredentialKey
    label: str
    secret_name: str
    required: bool

    @property
    def meta_name(self) -> str:
        """The keyring entry that holds only this credential's last-updated time."""
        return META_PREFIX + self.secret_name


class UnsubKey(StrEnum):
    """The two keys each unsubscribe-token kid has."""

    K_TOK = "k_tok"
    K_EDGE = "k_edge"


@dataclass(frozen=True, slots=True)
class UnsubKeyCredential:
    """One key of the per-kid unsubscribe-token family; see the module docstring."""

    key: UnsubKey
    label: str
    secret_prefix: str
    required: bool = True

    def secret_name(self, kid: int) -> str:
        """The keyring entry that holds this key for ``kid``: ``<secret_prefix>/<kid>``."""
        return f"{self.secret_prefix}/{check_kid(kid)}"

    def meta_name(self, kid: int) -> str:
        """The keyring entry that holds only this key's last-updated time for ``kid``."""
        return META_PREFIX + self.secret_name(kid)


# Errors ---------------------------------------------------------------------------------------


class UnknownCredential(DomainError):
    """A key ``REGISTRY`` doesn't declare."""

    code = "unknown_credential"

    def __init__(self, key: object) -> None:
        named = isinstance(key, str) and KEY_PATTERN.fullmatch(key) is not None
        super().__init__(f"unknown credential: {key if named else '(not a credential name)'}")
        self.key = str(key) if named else None


class InvalidCredentialValue(DomainError):
    """A value the Credentials page refuses to store; ``reason`` says what a valid value is,
    never what the value was."""

    code = "invalid_credential"

    def __init__(self, key: str, reason: str) -> None:
        super().__init__(f"{key}: {reason}")
        self.key = str(key)
        self.reason = reason


# Times ----------------------------------------------------------------------------------------


def encode_updated_at(at: datetime) -> str:
    """The text of a ``meta/<name>`` entry: ``at`` in UTC as ``YYYY-MM-DDTHH:MM:SS.ffffffZ``.
    Raises ``ValueError`` when ``at`` is naive."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("updated_at must be a timezone-aware datetime")
    return at.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def decode_updated_at(text: object) -> datetime | None:
    """The aware UTC time ``text`` holds, or ``None`` when it isn't exactly an
    ``encode_updated_at`` text."""
    if not isinstance(text, str) or UPDATED_AT_PATTERN.fullmatch(text) is None:
        return None
    try:
        return datetime.strptime(text, UPDATED_AT_FORMAT).replace(tzinfo=UTC)
    except ValueError:  # an impossible date or time, such as 2026-02-30
        return None


# The registry ---------------------------------------------------------------------------------


def _declare(*credentials: Credential) -> Mapping[str, Credential]:
    """The registry mapping of ``credentials``, in declaration order. Raises ``TypeError`` for
    a key that isn't a ``CredentialKey`` member, a repeated key, or a ``CredentialKey`` member
    left undeclared."""
    registry: dict[str, Credential] = {}
    for found in credentials:
        if not isinstance(found.key, CredentialKey):
            raise TypeError(f"credential {found.key!r} is not a CredentialKey member")
        if found.key in registry:
            raise TypeError(f"credential {found.key} is declared twice")
        registry[found.key] = found
    if missing := [str(key) for key in CredentialKey if key not in registry]:
        raise TypeError(f"CredentialKey members not declared: {', '.join(missing)}")
    return MappingProxyType(registry)


def credential(key: str) -> Credential:
    """The declared credential ``key``; raises ``UnknownCredential``."""
    found = REGISTRY.get(key) if isinstance(key, str) else None
    if found is None:
        raise UnknownCredential(key)
    return found


def declared() -> tuple[Credential, ...]:
    """Every declared credential, in declaration order."""
    return tuple(REGISTRY.values())


REGISTRY: Mapping[str, Credential] = _declare(
    Credential(CredentialKey.CLAUDE, "Claude API key", "anthropic/api_key", True),
    Credential(
        CredentialKey.GOOGLE_PLACES, "Google Places API key", "google_places/api_key", False
    ),
    Credential(CredentialKey.HUNTER, "Hunter API key", "hunter/api_key", True),
    Credential(CredentialKey.PROSPEO, "Prospeo API key", "prospeo/api_key", False),
    Credential(
        CredentialKey.COMPANIES_HOUSE, "Companies House API key", "companies_house/api_key", True
    ),
    Credential(CredentialKey.TELEGRAM_BOT, "Telegram bot token", "telegram/bot_token", True),
    Credential(
        CredentialKey.UNSUBSCRIBE_BEARER,
        "Unsubscribe Endpoint bearer secret",
        "unsub/bearer",
        True,
    ),
    Credential(CredentialKey.BACKUP_PASSPHRASE, "Backup passphrase", "backup/passphrase", False),
    Credential(
        CredentialKey.JOB_AGGREGATOR, "Job aggregator API key", "job_aggregator/api_key", False
    ),
)


def unsub_key(key: str) -> UnsubKeyCredential:
    """The declared unsubscribe-token key ``key`` (``k_tok`` or ``k_edge``); raises
    ``UnknownCredential``."""
    found = UNSUB_KEYS.get(key) if isinstance(key, str) else None
    if found is None:
        raise UnknownCredential(key)
    return found


def declared_unsub_keys() -> tuple[UnsubKeyCredential, ...]:
    """Both unsubscribe-token keys, ``k_tok`` then ``k_edge``."""
    return tuple(UNSUB_KEYS.values())


UNSUB_KEYS: Mapping[str, UnsubKeyCredential] = MappingProxyType(
    {
        UnsubKey.K_TOK: UnsubKeyCredential(
            UnsubKey.K_TOK, "Token encryption key (k_tok)", "unsub/k_tok"
        ),
        UnsubKey.K_EDGE: UnsubKeyCredential(
            UnsubKey.K_EDGE, "Worker tag key (k_edge)", "unsub/k_edge"
        ),
    }
)
