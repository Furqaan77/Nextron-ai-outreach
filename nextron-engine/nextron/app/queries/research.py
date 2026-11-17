"""Read API of the ``research`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.research`` only. All read through the
current unit of work, read-only ones included.

- ``contacts(lead_id)``: the Lead's Contacts as ``StoredContact`` rows, oldest first (by
  ``created_at``, then ``id``); empty for an unknown Lead (a text that isn't a ULID included).
- ``provenance(contact_id)``: the Contact's Provenance Records as ``StoredProvenance`` rows, by
  ``retrieved_at``, then ``id``; empty for an unknown Contact.
"""

from nextron.db.repos.research import contacts as contacts_repo
from nextron.db.repos.research import provenance as provenance_repo
from nextron.db.repos.research.contacts import StoredContact
from nextron.db.repos.research.provenance import StoredProvenance
from nextron.domain.ids import is_ulid

__all__ = ["StoredContact", "StoredProvenance", "contacts", "provenance"]


def contacts(lead_id: str) -> tuple[StoredContact, ...]:
    """The Contacts of the Lead ``lead_id``, oldest first."""
    if not is_ulid(lead_id):
        return ()
    return contacts_repo.for_lead(lead_id)


def provenance(contact_id: str) -> tuple[StoredProvenance, ...]:
    """The Provenance Records of the Contact ``contact_id``, oldest first."""
    if not is_ulid(contact_id):
        return ()
    return provenance_repo.for_contact(contact_id)
