"""The one redaction rule (AD-17 and the *Logging* convention; NFR-18).

``redact`` masks the secrets and identifiers listed below in free text. The logging filter
applies it to every message, argument, traceback and string ``extra`` value; ``AdapterError``
applies it to its message; and code that stores an error string (for example
``jobs.last_error``) applies it before writing. It is pure, uses the standard library only, and
is idempotent: ``redact(redact(text)) == redact(text)``.

It masks exactly these, in this order, so an earlier rule removes a value before a later rule
could leave part of it behind, and repeats the rules until a pass changes nothing:

1. **URL query strings and fragments.** In a whitespace-delimited token, at the first ``?`` or
   ``#`` that has a ``/`` before it in the token or directly follows a dotted host name
   (``api.example.com?...``, ``10.0.0.1:8080#...``), everything up to the next whitespace is
   dropped. Absolute URLs keep scheme, host and path; relative paths, such as Uvicorn's
   ``GET /enter?code=...``, keep their path. A ``?`` or ``#`` that ends a token is left alone.
2. **URL userinfo:** ``//user:pass@`` becomes ``//<redacted>@``.
3. **Telegram bot tokens:** ``/bot<id>:<token>`` becomes ``/bot<redacted>``.
4. **Authorization schemes:** ``Bearer <token>`` and ``Basic <token>``, in any case, become
   ``Bearer <redacted>`` and ``Basic <redacted>``.
5. **Credential fields:** ``api_key`` (also ``api-key``, ``apikey``), ``token``, ``secret``,
   ``password``, ``client_secret``, ``access_token`` and ``refresh_token``, in any case and as
   the end of a longer name (``x-api-key``, ``id_token``), followed by ``=`` or ``:`` and a
   value of any length (``api_key=...``, ``x-api-key: ...``, ``"password": "..."``). The name
   stays and the value becomes ``<redacted>``.
6. **Email addresses,** including Unicode ones and ones URL-encoded with ``%40``, become
   ``<email>``. Local-part characters left in front of it (a local part over 64 characters) are
   absorbed into it.
7. **Anthropic keys:** ``sk-ant-...`` becomes ``<key>``.
8. **Standard base64:** a run of 32 or more ``[A-Za-z0-9+/]`` characters, with up to two ``=``,
   that contains ``+`` or ends with ``=`` becomes ``<key>``. A run whose only other character
   is ``/`` is a path such as ``/leads/<ULID>``: it is left to rule 9 segment by segment.
9. **Long keys:** any other run of 32 or more ``[A-Za-z0-9_-]`` characters becomes ``<key>``.

A 26-character ULID matches none of these, so IDs survive. Nothing else is masked: phone
numbers, names, postal addresses and other free text pass through unchanged, so log IDs, never
personal data. The rules over-mask rather than under-mask: a 32-character identifier, or the
word after ``bearer`` or ``basic`` in prose, is masked too.
"""

import re
import string
from typing import Final

REDACTED: Final = "<redacted>"
EMAIL: Final = "<email>"
KEY: Final = "<key>"

# A token that holds "?" or "#"; the look-behind starts it at a token boundary, which keeps the
# scan linear.
_QUERY_TOKEN: Final = re.compile(r"(?<!\S)[^\s?#]*[?#]\S*")
_QUERY_START: Final = re.compile(r"[?#]")
_HOST_CHARS: Final = frozenset(string.ascii_letters + string.digits + ".-")
_PORT_DIGITS: Final = frozenset(string.digits)
_HOSTNAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*(?:\.[A-Za-z0-9][A-Za-z0-9-]*)+")
_MAX_HOSTNAME: Final = 253
_USERINFO: Final = re.compile(r"//[^\s/?#]+@")
_TELEGRAM_BOT: Final = re.compile(r"/bot[0-9]+:[A-Za-z0-9_-]+")
# The token stops at characters an authorization token never holds, so a following "," or ")"
# stays, and at "?" and "#", so rule 1 sees the same text on a second pass.
_AUTH_SCHEME: Final = re.compile(
    r"\b(bearer|basic)[ \t]+[^\s\"'`<>,;?#()\[\]{}]+", re.IGNORECASE
)
_CREDENTIAL: Final = re.compile(
    r"(?<![A-Za-z0-9])(?P<field>"
    r"(?:api[_-]?key|client_secret|access_token|refresh_token|token|secret|password)"
    r"[\"']?[ \t]*[:=][ \t]*[\"']?)[^\s\"'`&,;]+",
    re.IGNORECASE,
)
# The local part is capped at RFC 5321's 64 characters, which keeps the scan linear on long runs
# without an "@"; _EMAIL_PREFIX then absorbs whatever a longer local part left in front.
_EMAIL: Final = re.compile(
    r"[\w.%+-]{1,64}(?:@|%40)(?:[^\W_]|-)+(?:\.(?:[^\W_]|-)+)+", re.IGNORECASE
)
_EMAIL_PREFIX: Final = re.compile(rf"(?<![\w.%+-])[\w.%+-]+(?={EMAIL})")
_ANTHROPIC_KEY: Final = re.compile(r"sk-ant-[A-Za-z0-9_-]*")
_BASE64: Final = re.compile(r"[A-Za-z0-9+/]{32,}={0,2}")
_LONG_KEY: Final = re.compile(r"[A-Za-z0-9_-]{32,}")
_MAX_PASSES: Final = 8


def _ends_with_hostname(token: str, end: int) -> bool:
    """Whether ``token[:end]`` ends with a dotted host name, with or without a ``:port``."""
    digits = end
    while digits > 0 and token[digits - 1] in _PORT_DIGITS and end - digits < 6:
        digits -= 1
    if digits < end and digits > 0 and token[digits - 1] == ":":
        end = digits - 1
    start = end
    while start > 0 and token[start - 1] in _HOST_CHARS:
        start -= 1
        if end - start > _MAX_HOSTNAME:
            return False
    return _HOSTNAME.fullmatch(token, start, end) is not None


def _strip_query(match: re.Match[str]) -> str:
    token = match.group()
    slash = token.find("/")
    for found in _QUERY_START.finditer(token):
        at = found.start()
        if at == len(token) - 1:
            break
        if 0 <= slash < at or _ends_with_hostname(token, at):
            return token[:at]
    return token


def _auth_scheme(match: re.Match[str]) -> str:
    return f"{match.group(1).capitalize()} {REDACTED}"


def _base64(match: re.Match[str]) -> str:
    run = match.group()
    return KEY if "+" in run or run.endswith("=") else run


def _redact_once(text: str) -> str:
    text = _QUERY_TOKEN.sub(_strip_query, text)
    text = _USERINFO.sub(f"//{REDACTED}@", text)
    text = _TELEGRAM_BOT.sub(f"/bot{REDACTED}", text)
    text = _AUTH_SCHEME.sub(_auth_scheme, text)
    text = _CREDENTIAL.sub(rf"\g<field>{REDACTED}", text)
    text = _EMAIL.sub(EMAIL, text)
    text = _EMAIL_PREFIX.sub("", text)
    text = _ANTHROPIC_KEY.sub(KEY, text)
    text = _BASE64.sub(_base64, text)
    return _LONG_KEY.sub(KEY, text)


def redact(text: str) -> str:
    """Return ``text`` with the values listed in the module docstring masked.

    The rules run again until a pass changes nothing, because masking one value can expose
    another (a credential value that held a ``/`` can hide URL userinfo behind it). Real text
    settles in one or two passes; ``_MAX_PASSES`` only bounds a pathological input.
    """
    for _ in range(_MAX_PASSES):
        redacted = _redact_once(text)
        if redacted == text:
            break
        text = redacted
    return text
