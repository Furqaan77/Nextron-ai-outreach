"""The port-boundary error (AD-9 and the *Errors* convention).

An adapter catches every SDK, HTTP and OS exception at the port boundary and raises
``AdapterError(retryable, code, message)`` in its place, so app services and jobs never see a
third-party exception and can tell what to retry:

- ``retryable``: ``True`` when the same call may succeed later (a timeout, a 429, a 5xx), and
  ``False`` when it never will (bad credentials, a rejected request). Jobs retry only
  retryable errors.
- ``code``: a short, stable machine code such as ``rate_limited``, matching the domain's
  ``CODE_PATTERN`` (``^[a-z][a-z0-9_]*$``).
- ``message``: written by the adapter, never the SDK's own text. It is redacted with
  ``nextron.domain.redaction.redact`` when the error is built, so it is safe to log and to store
  (for example as ``jobs.last_error``).

``raise AdapterError(...) from exc`` keeps the SDK exception as the cause, for the logged
traceback, which the logging filter redacts. ``tests/contracts/test_error_contracts.py`` fails
when a module under ``nextron.adapters`` raises anything but ``AdapterError`` (or a subclass),
re-raises with a bare ``raise``, or passes the caught exception's text into the error.
"""

from typing import Self

from nextron.domain.errors import CODE_PATTERN
from nextron.domain.redaction import redact


class AdapterError(Exception):
    """An adapter call failed; ``message`` is the adapter's own, redacted text.

    Raises ``TypeError`` when ``retryable`` is not a ``bool`` or ``code`` or ``message`` is not
    a ``str``, and ``ValueError`` when ``code`` doesn't match ``CODE_PATTERN``.
    """

    def __init__(self, retryable: bool, code: str, message: str) -> None:
        if not isinstance(retryable, bool):
            raise TypeError(f"retryable must be a bool, not {type(retryable).__name__}")
        if not isinstance(code, str):
            raise TypeError(f"code must be a str, not {type(code).__name__}")
        if not CODE_PATTERN.fullmatch(code):
            raise ValueError(f"code {code!r} must match ^[a-z][a-z0-9_]*$")
        if not isinstance(message, str):
            raise TypeError(f"message must be a str, not {type(message).__name__}")
        self.retryable = retryable
        self.code = code
        self.message = redact(message)
        super().__init__(self.message)

    def __reduce__(self) -> tuple[type[Self], tuple[bool, str, str]]:
        # Exception pickles and copies by calling the class with ``args``, which holds only the
        # message; rebuild from all three fields instead.
        return (type(self), (self.retryable, self.code, self.message))
