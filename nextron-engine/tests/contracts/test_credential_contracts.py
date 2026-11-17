"""Credential contracts (AD-12, NFR-18), each with a live proof on a probe registry.

- **Every keyring name is valid.** Each credential's ``secret_name`` and ``meta_name`` passes
  ``is_secret_name``, so ``SecretsPort`` never refuses a declared credential.
- **Names don't collide.** No two credentials share a ``secret_name`` (so none share a
  ``meta_name``), and no ``secret_name`` starts with ``meta/`` (the companion time entries),
  ``session/`` (the dashboard's one-time code), ``google/`` (the Google client and grants) or
  ``gmail/`` (the Sending Inboxes, Epic 6), so a credential can never overwrite another entry.
- **The registry is keyed by its own keys.** Each entry's mapping key is its ``Credential.key``,
  a ``CredentialKey`` member, and every member is declared.
- **Keys are safe in markup.** Each key fullmatches ``KEY_PATTERN`` (``[a-z][a-z0-9_]{0,30}``),
  so it can go into ``id="credential-<key>"`` and ``hx-target="#credential-<key>"`` as it is.
- **Labels are unique,** so no two rows of the Credentials page look the same.
- **The unsubscribe token key family** (Story 3.8, ``UNSUB_KEYS``) keeps the same rules for
  every kid from 1 to 255: each ``secret_name(kid)`` and ``meta_name(kid)`` is a valid secret
  name, none uses a reserved prefix or is a ``REGISTRY`` credential's name, no two family
  members share one, each mapping key is its ``UnsubKey`` (every member declared) and fits
  ``KEY_PATTERN``, and no label is another family member's or a credential's.
"""

from collections.abc import Mapping
from typing import Final

from nextron.domain import credentials as registry
from nextron.domain.credentials import (
    KEY_PATTERN,
    Credential,
    CredentialKey,
    UnsubKey,
    UnsubKeyCredential,
)
from nextron.ports.secrets import is_secret_name

RESERVED_PREFIXES: Final = ("meta/", "session/", "google/", "gmail/")
LONG_NAME: Final = "extra/" + "x" * 200  # over MAX_SECRET_NAME_LENGTH


def credential_problems(credentials: Mapping[str, Credential]) -> list[str]:
    """Everything wrong with ``credentials``, as ``"<key>: <why>"``."""
    problems = []
    owners: dict[str, str] = {}
    labelled: dict[str, str] = {}
    for key, found in credentials.items():
        if found.key != key or not isinstance(found.key, CredentialKey):
            problems.append(f"{key}: the mapping key is not the credential's CredentialKey")
        if not (isinstance(key, str) and KEY_PATTERN.fullmatch(key)):
            problems.append(f"{key}: the key doesn't match KEY_PATTERN, so it isn't safe in an id")
        for attribute in ("secret_name", "meta_name"):
            name = getattr(found, attribute)
            if not is_secret_name(name):
                problems.append(f"{key}: {attribute} {name!r} is not a valid secret name")
        if found.secret_name.startswith(RESERVED_PREFIXES):
            problems.append(f"{key}: secret_name {found.secret_name!r} uses a reserved prefix")
        owner = owners.setdefault(found.secret_name, key)
        if owner != key:
            problems.append(f"{key}: secret_name {found.secret_name!r} is also {owner}'s")
        first = labelled.setdefault(found.label, key)
        if first != key:
            problems.append(f"{key}: label {found.label!r} is also {first}'s")
    if missing := [str(member) for member in CredentialKey if member not in credentials]:
        problems.append(f"CredentialKey members not declared: {', '.join(missing)}")
    return problems


def test_every_declared_credential_keeps_the_contracts() -> None:
    assert credential_problems(registry.REGISTRY) == []
    assert len(registry.declared()) == len(CredentialKey) == 9


def _probe(key: str, name: str, label: str | None = None) -> Credential:
    return Credential(key, label or f"Probe {key}", name, False)  # type: ignore[arg-type]


def test_the_check_catches_a_bad_probe_registry() -> None:
    bad_key = 'x" onclick="go()'
    probe: dict[str, Credential] = {str(found.key): found for found in registry.declared()}
    probe.update(
        {
            "claude": _probe(CredentialKey.CLAUDE, "google_places/api_key"),  # google's name
            "hunter": _probe(CredentialKey.HUNTER, "Hunter/API key"),  # not a valid name
            "prospeo": _probe(CredentialKey.PROSPEO, "meta/prospeo/api_key"),  # reserved
            "telegram_bot": _probe(CredentialKey.TELEGRAM_BOT, "session/open_code"),  # reserved
            "backup_passphrase": _probe(CredentialKey.JOB_AGGREGATOR, "backup/passphrase"),
            "job_aggregator": _probe(  # another row's label
                CredentialKey.JOB_AGGREGATOR, "job_aggregator/api_key", "Google Places API key"
            ),
            "extra": _probe("extra", LONG_NAME),
            bad_key: _probe(bad_key, "bad/key"),  # would break out of id="credential-..."
            "Upper_Key": _probe("Upper_Key", "upper/key"),
        }
    )
    del probe["companies_house"]

    assert credential_problems(probe) == [
        "google_places: secret_name 'google_places/api_key' is also claude's",
        "hunter: secret_name 'Hunter/API key' is not a valid secret name",
        "hunter: meta_name 'meta/Hunter/API key' is not a valid secret name",
        "prospeo: secret_name 'meta/prospeo/api_key' uses a reserved prefix",
        "telegram_bot: secret_name 'session/open_code' uses a reserved prefix",
        "backup_passphrase: the mapping key is not the credential's CredentialKey",
        "job_aggregator: label 'Google Places API key' is also google_places's",
        "extra: the mapping key is not the credential's CredentialKey",
        f"extra: secret_name {LONG_NAME!r} is not a valid secret name",
        f"extra: meta_name {'meta/' + LONG_NAME!r} is not a valid secret name",
        f"{bad_key}: the mapping key is not the credential's CredentialKey",
        f"{bad_key}: the key doesn't match KEY_PATTERN, so it isn't safe in an id",
        "Upper_Key: the mapping key is not the credential's CredentialKey",
        "Upper_Key: the key doesn't match KEY_PATTERN, so it isn't safe in an id",
        "CredentialKey members not declared: companies_house",
    ]



def unsub_key_problems(
    family: Mapping[str, UnsubKeyCredential], credentials: Mapping[str, Credential]
) -> list[str]:
    """Everything wrong with the unsubscribe token key ``family``, as ``"<key>: <why>"``."""
    problems = []
    taken = {found.secret_name: key for key, found in credentials.items()}
    labels = {found.label: key for key, found in credentials.items()}
    for key, found in family.items():
        if found.key != key or not isinstance(found.key, UnsubKey):
            problems.append(f"{key}: the mapping key is not the key's UnsubKey")
        if not (isinstance(key, str) and KEY_PATTERN.fullmatch(key)):
            problems.append(f"{key}: the key doesn't match KEY_PATTERN, so it isn't safe in an id")
        bad = sorted(
            {
                kind
                for kid in range(1, 256)
                for kind, name in (
                    ("secret_name", found.secret_name(kid)),
                    ("meta_name", found.meta_name(kid)),
                )
                if not is_secret_name(name)
            }
        )
        problems += [f"{key}: a {kind} is not a valid secret name" for kind in bad]
        for kid in range(1, 256):
            name = found.secret_name(kid)
            if name.startswith(RESERVED_PREFIXES):
                problems.append(f"{key}: secret_name {name!r} uses a reserved prefix")
                break
            owner = taken.setdefault(name, key)
            if owner != key:
                problems.append(f"{key}: secret_name {name!r} is also {owner}'s")
                break
        first = labels.setdefault(found.label, key)
        if first != key:
            problems.append(f"{key}: label {found.label!r} is also {first}'s")
    if missing := [str(member) for member in UnsubKey if member not in family]:
        problems.append(f"UnsubKey members not declared: {', '.join(missing)}")
    return problems


def test_the_unsubscribe_token_key_family_keeps_the_contracts() -> None:
    assert unsub_key_problems(registry.UNSUB_KEYS, registry.REGISTRY) == []
    assert [found.key for found in registry.declared_unsub_keys()] == list(UnsubKey)


def test_the_family_check_catches_a_bad_probe_family() -> None:
    probe = {
        "k_tok": UnsubKeyCredential(UnsubKey.K_TOK, "Claude API key", "unsub/k_tok"),
        "k_edge": UnsubKeyCredential(UnsubKey.K_TOK, "Edge", "meta/edge"),
        "other": UnsubKeyCredential(UnsubKey.K_EDGE, "Other", "unsub/k_tok"),
        "Bad Key": UnsubKeyCredential("Bad Key", "Bad", "Bad Key"),  # type: ignore[arg-type]
    }

    assert unsub_key_problems(probe, registry.REGISTRY) == [
        "k_tok: label 'Claude API key' is also claude's",
        "k_edge: the mapping key is not the key's UnsubKey",
        "k_edge: secret_name 'meta/edge/1' uses a reserved prefix",
        "other: the mapping key is not the key's UnsubKey",
        "other: secret_name 'unsub/k_tok/1' is also k_tok's",
        "Bad Key: the mapping key is not the key's UnsubKey",
        "Bad Key: the key doesn't match KEY_PATTERN, so it isn't safe in an id",
        "Bad Key: a meta_name is not a valid secret name",
        "Bad Key: a secret_name is not a valid secret name",
    ]
    only_k_tok = {"k_tok": registry.UNSUB_KEYS["k_tok"]}
    assert unsub_key_problems(only_k_tok, registry.REGISTRY) == [
        "UnsubKey members not declared: k_edge"
    ]
