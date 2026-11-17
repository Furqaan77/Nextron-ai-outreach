"""The email syntax check (``EmailSyntaxPort``, FR-15, FR-59) over email-validator.

Import rule: ``nextron.ports``, ``email_validator`` and the standard library. It is pure
computation: no DNS lookup, no network, no file and no log line. Contract 4 lets only adapters
import ``email_validator``.

``EmailValidatorSyntax().is_valid(address)`` calls ``email_validator.validate_email(address,
check_deliverability=False, allow_smtputf8=True, allow_quoted_local=False,
allow_domain_literal=False, allow_display_name=False, allow_empty_local=False,
test_environment=False, globally_deliverable=True)`` and answers ``True`` when it returns and
``False`` on ``EmailNotValidError``. So a quoted local part, a domain literal, a display name,
a special-use or test domain (``.test``, ``.local``, ``.localhost``) and a domain that isn't
globally deliverable all fail, without a DNS query. A non-``str`` raises ``TypeError``
through the port's ``checked_address`` (the library would also take ``bytes``); no other
exception leaves it.
"""

from email_validator import EmailNotValidError, validate_email

from nextron.ports.email_syntax import checked_address


class EmailValidatorSyntax:
    """``EmailSyntaxPort`` over email-validator, offline."""

    def is_valid(self, address: str) -> bool:
        """Whether ``address`` passes email-validator's syntax checks; see the module
        docstring."""
        text = checked_address(address)
        try:
            validate_email(
                text,
                check_deliverability=False,
                allow_smtputf8=True,
                allow_quoted_local=False,
                allow_domain_literal=False,
                allow_display_name=False,
                allow_empty_local=False,
                test_environment=False,
                globally_deliverable=True,
            )
        except EmailNotValidError:
            return False
        return True
