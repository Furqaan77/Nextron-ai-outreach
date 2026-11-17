"""The ``EmailSyntaxPort`` (AD-9, FR-15, FR-59): whether an email address is well-formed.

A syntax check only: an implementation reaches no DNS, mail server or other external system,
so it is free, needs no credential and is not metered. ``is_valid(address)`` is ``True`` for an
address that is syntactically deliverable and ``False`` for any other. Every implementation
first passes its argument through ``checked_address``, which raises ``TypeError`` for a
non-``str`` (a programming error, not an adapter failure, so it is no ``AdapterError``).
Callers normalise the address first (``nextron.domain.identity.normalize_email``) and never log
it.

``nextron.adapters.email_syntax.EmailValidatorSyntax`` is the adapter (over email-validator,
with deliverability checks off); tests use ``tests.fakes.email_syntax.FakeEmailSyntax``. The MX
and disposable-domain checks come with Story 2.14.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmailSyntaxPort(Protocol):
    """Checks an email address's syntax, offline."""

    def is_valid(self, address: str) -> bool:
        """Whether ``address`` is a syntactically valid email address."""
        ...


def checked_address(address: object) -> str:
    """``address`` itself when it is a ``str``; raises ``TypeError`` otherwise."""
    if not isinstance(address, str):
        raise TypeError(f"is_valid takes a str, not {type(address).__name__}")
    return address
