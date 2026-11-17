"""Public command API of the ``compliance`` service (AD-3, AD-4, AD-16, AD-17, FR-50, FR-63).

Other services and the delivery layers change ``compliance`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Compliance(clock)`` owns the Suppression List (``suppression_entries``) and applies the
SuppressionEntry machine (``nextron.domain.suppression_entry``). Its ``suppress`` is the only
code that writes the list (``tests/contracts/test_suppression_contracts.py``). An entry is
permanent: nothing here, or anywhere, removes one or ages one out.

- ``@command suppress(subject, scope, source, cause_ref) -> SuppressResult(written, existing)``:

  1. **Arguments.** ``subject`` must be a ``ContactSubject``, ``scope`` a ``SuppressionScope``
     and ``source`` a ``SuppressionSource`` (otherwise ``TypeError``); ``cause_ref`` must be
     ``None`` or a ULID (otherwise ``ValueError``).
  2. **Reads.** The Contact through ``queries.research.contact`` and its Lead through
     ``queries.discovery.lead``; either missing raises ``LookupError``, whose message holds no
     id or value.
  3. **Keys.** ``person_keys`` of the Contact's email, name and LinkedIn URL with the Lead's
     ``web_domain``, then, for scope ``firm``, ``firm_domain_keys`` of the email and the web
     domain (``nextron.domain.contactability``), each (``kind``, ``value_hash``) once. A value
     that can't be normalised is no key, so a Contact with no known key gives
     ``SuppressResult((), ())``: nothing is written or published.
  4. **Writes.** A key that already has an ``active`` entry adds that entry's id to
     ``existing`` and writes nothing. Each other key gets a new ``active`` entry (its
     ``domain_plain`` only for a domain key) and one ``events`` row: entity
     ``suppression_entries``, ``None`` to ``active``, cause ``CREATION[source].cause`` (the
     source itself), the ``cause_ref``, the actor ``ACTORS[source]`` (``operator`` for the
     ``operator``, ``import`` and ``dsr`` sources; ``system:compliance`` for the opt-outs), at
     the clock's now. Its id goes to ``written``, in key order.
  5. **Publish.** With at least one key, ``SubjectSuppressed(suppression_entry_id=(written +
     existing)[0], source, contact_id, lead_id)`` is published, even when every key was already
     ``active``, so a later opt-out still closes the Lead. Its subscribers
     (``nextron.main.build_subscribers``) run before commit, and any exception in one rolls the
     entries back with it.

  Nothing is logged: the ``events`` rows are the audit, and neither they nor the notification
  hold an address, a name or a URL.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import research as research_queries
from nextron.db import eventlog
from nextron.db.repos.compliance import suppression_entries as entries_repo
from nextron.db.uow import command, current
from nextron.domain.contactability import (
    ContactSubject,
    SuppressionKey,
    SuppressionScope,
    firm_domain_keys,
    person_keys,
)
from nextron.domain.ids import is_ulid
from nextron.domain.notifications import SubjectSuppressed
from nextron.domain.suppression_entry import CREATION, OPT_OUT_SOURCES, SuppressionSource
from nextron.ports.clock import Clock

ENTITY: Final = "suppression_entries"
OPERATOR_ACTOR: Final = "operator"
SYSTEM_ACTOR: Final = "system:compliance"
ACTORS: Final[Mapping[SuppressionSource, str]] = MappingProxyType(
    {
        source: SYSTEM_ACTOR if source in OPT_OUT_SOURCES else OPERATOR_ACTOR
        for source in SuppressionSource
    }
)


@dataclass(frozen=True, slots=True)
class SuppressResult:
    """The ids of the entries ``suppress`` wrote, and of the ``active`` ones it found."""

    written: tuple[str, ...]
    existing: tuple[str, ...]


def _check_arguments(subject: object, scope: object, source: object, cause_ref: object) -> None:
    if not isinstance(subject, ContactSubject):
        raise TypeError(f"suppress takes a ContactSubject, not {type(subject).__name__}")
    if not isinstance(scope, SuppressionScope):
        raise TypeError(f"suppress takes a SuppressionScope, not {type(scope).__name__}")
    if not isinstance(source, SuppressionSource):
        raise TypeError(f"suppress takes a SuppressionSource, not {type(source).__name__}")
    if not (cause_ref is None or is_ulid(cause_ref)):
        raise ValueError("suppress: cause_ref must be None or a ULID")


def _unique(keys: tuple[SuppressionKey, ...]) -> tuple[SuppressionKey, ...]:
    """``keys`` in order, each (``kind``, ``value_hash``) once."""
    seen: set[tuple[str, str]] = set()
    found = []
    for key in keys:
        identity = (str(key.kind), key.value_hash)
        if identity not in seen:
            seen.add(identity)
            found.append(key)
    return tuple(found)


class Compliance:
    """The Suppression List; see the module docstring."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @command
    def suppress(
        self,
        subject: ContactSubject,
        scope: SuppressionScope,
        source: SuppressionSource,
        cause_ref: str | None,
    ) -> SuppressResult:
        """Write an ``active`` entry for every known key of ``subject``; see the module
        docstring."""
        _check_arguments(subject, scope, source, cause_ref)
        contact = research_queries.contact(subject.contact_id)
        if contact is None:
            raise LookupError("suppress: the Contact is unknown")
        lead = discovery_queries.lead(contact.lead_id)
        if lead is None:
            raise LookupError("suppress: the Contact's Lead is unknown")
        keys = person_keys(
            email=contact.email,
            name=contact.name,
            linkedin_url=contact.linkedin_url,
            web_domain=lead.web_domain,
        )
        if scope is SuppressionScope.FIRM:
            keys += firm_domain_keys(email=contact.email, web_domain=lead.web_domain)
        keys = _unique(keys)
        if not keys:
            return SuppressResult((), ())
        change = CREATION[source]
        now = self._clock.now()
        written: list[str] = []
        existing: list[str] = []
        for key in keys:
            found = entries_repo.active_for(str(key.kind), key.value_hash)
            if found is not None:
                existing.append(found.id)
                continue
            entry_id = entries_repo.insert(
                kind=str(key.kind),
                value_hash=key.value_hash,
                domain_plain=key.domain_plain,
                source=str(source),
                cause_ref=cause_ref,
                created_at=now,
            )
            eventlog.append(
                entity=ENTITY,
                entity_id=entry_id,
                from_status=None,
                to_status=str(change.to),
                cause=str(change.cause),
                cause_ref=cause_ref,
                actor=ACTORS[source],
                at=now,
            )
            written.append(entry_id)
        result = SuppressResult(tuple(written), tuple(existing))
        current().publish(
            SubjectSuppressed(
                suppression_entry_id=(result.written + result.existing)[0],
                source=str(source),
                contact_id=contact.id,
                lead_id=lead.id,
            )
        )
        return result
