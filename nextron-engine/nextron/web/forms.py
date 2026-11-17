"""Form bodies of dashboard POSTs (AD-13): urlencoded forms, and the one upload form.

A POST handler takes its form through the ``async`` dependency ``form_fields``, as a parameter
``fields: Annotated[FormFields, Depends(form_fields)]``; never FastAPI's ``Form()``, which needs
``python-multipart``. The route class puts ``require_csrf`` first in every POST's dependencies,
so the CSRF check has already run, and Starlette has cached the body it read. ``form_fields``
parses that body and needs:

- exactly one ``Content-Type`` header, whose media type is ``application/x-www-form-urlencoded``
  (a multipart or JSON body is refused);
- an ASCII body, whose percent-escapes decode as strict UTF-8;
- what ``parse_qs`` accepts with ``keep_blank_values=True, strict_parsing=True,
  max_num_fields=1000``.

It returns ``{name: [value, ...]}``, ``csrf_token`` included. Anything else raises
``MalformedForm``, and so does a handler that finds fields other than its form's own.
``malformed_form``, the exception handler ``create_app`` registers, answers 400
``Request refused: malformed form`` (plain text, ``Cache-Control: no-store``) and logs
``request_refused`` with ``check=form``, the fixed ``reason`` (``not_urlencoded``,
``not_multipart``, ``bad_body``, ``wrong_fields`` or ``repeated_field``), the ``method`` and the
``path``; never a field name or value. The form stays as it was, and HTMX puts the 400's text in
the page's error notice (``#request-error``, see ``nextron.web.app``).

``require_csrf`` runs first and parses an urlencoded body itself, so a repeated
``Content-Type``, a non-ASCII urlencoded body or more than 1,000 fields get its 403
(``Request refused: security check failed``) before ``form_fields`` sees them. The same checks
stay here as defence in depth; what reaches this module's 400 is a body that isn't urlencoded,
a bad escape or field (``%FF``, ``&&``, a field without ``=``), and fields other than the
form's own.

**Uploads (Story 2.3).** A form that sends a file takes its body through the ``async``
dependency ``upload_form`` instead, as ``form: Annotated[UploadForm, Depends(upload_form)]``.
It is a strict standard-library ``multipart/form-data`` parser:

- **The request** needs exactly one ``Content-Type`` whose media type is
  ``multipart/form-data`` with exactly one ``boundary`` parameter of 1 to 70 RFC 2046
  characters (letters, digits, ``'()+_,-./:=?`` and inner spaces), quoted or not; otherwise
  ``MalformedForm("not_multipart")``.
- **Size.** The body is read through ``request.stream()``. Once it passes
  ``MAX_UPLOAD_BODY`` (5 MiB + 64 KiB), the rest is drained and discarded, nothing is parsed,
  and the answer is ``UploadForm(too_large=True)``.
- **Parsing.** The body must be ``--B\r\n`` part (``\r\n--B\r\n`` part)* ``\r\n--B--``,
  with an optional final ``\r\n``, and no part may hold ``\r\n--B``. A part's headers end
  at its first ``\r\n\r\n`` and are strict UTF-8: exactly one ``Content-Disposition`` that
  fullmatches ``form-data; name="<name>"`` with an optional ``; filename="<file name>"``, and at
  most one ``Content-Type``; no other header. A part with a file name is a file (its bytes, in
  ``files``); any other is text, decoded as strict UTF-8 (in ``texts``). Anything else is
  ``MalformedForm("bad_body")``, and a name given twice ``MalformedForm("repeated_field")``.

The handler then refuses fields other than its form's own (``MalformedForm("wrong_fields")``).
``require_csrf`` reads no multipart body: an upload presents its token in the ``X-CSRF-Token``
header HTMX sends, and the hidden ``csrf_token`` field is one of the fields a handler allows.
File names are never kept, logged or shown.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import PlainTextResponse, Response

from nextron.web.csrf import FORM_MEDIA_TYPE, MAX_FORM_FIELDS, media_type

FORM_CHECK: Final = "form"
MALFORMED_FORM: Final = "Request refused: malformed form"
# Why a form was refused, as logged: fixed codes, never a field name or value.
NOT_URLENCODED: Final = "not_urlencoded"  # not exactly one urlencoded Content-Type
BAD_BODY: Final = "bad_body"  # not ASCII, a bad escape or field, or too many fields
WRONG_FIELDS: Final = "wrong_fields"  # fields other than the form's own
REPEATED_FIELD: Final = "repeated_field"  # a form field given more than once
NOT_MULTIPART: Final = "not_multipart"  # not exactly one multipart Content-Type with a boundary

MULTIPART_MEDIA_TYPE: Final = "multipart/form-data"
MAX_UPLOAD_BODY: Final = 5 * 1024 * 1024 + 64 * 1024
CRLF: Final = b"\r\n"
HEADER_END: Final = b"\r\n\r\n"
# RFC 2046 section 5.1.1: 1 to 70 bchars, the last not a space. Matched with fullmatch.
BOUNDARY_PATTERN: Final = re.compile(r"[0-9A-Za-z'()+_,./:=? -]{0,69}[0-9A-Za-z'()+_,./:=?-]")
PARAMETER_PATTERN: Final = re.compile(r'\s*([A-Za-z0-9_-]+)\s*=\s*("[^"]*"|[^\s";]*)\s*')
DISPOSITION_PATTERN: Final = re.compile(
    r'form-data; name="([^"\r\n]+)"(?:; filename="([^"\r\n]*)")?'
)

FormFields = dict[str, list[str]]  # each field name with its values, in body order

log = logging.getLogger(__name__)


class MalformedForm(Exception):
    """A POST body that isn't the form its handler expects; answered with 400. ``reason`` is
    one of the fixed codes above."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"malformed form: {reason}")
        self.check = FORM_CHECK
        self.reason = reason


async def form_fields(request: Request) -> FormFields:
    """The fields of the request's urlencoded body; raises ``MalformedForm``."""
    content_types = request.headers.getlist("content-type")
    if len(content_types) != 1 or media_type(content_types[0]) != FORM_MEDIA_TYPE:
        raise MalformedForm(NOT_URLENCODED)
    body = await request.body()
    try:
        return parse_qs(
            body.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=MAX_FORM_FIELDS,
            encoding="utf-8",
            errors="strict",
        )
    except ValueError as exc:  # non-ASCII, bad UTF-8 escapes, a bad field or too many fields
        raise MalformedForm(BAD_BODY) from exc


@dataclass(frozen=True, slots=True)
class UploadForm:
    """An upload form's fields: ``texts`` (name to text) and ``files`` (name to bytes), or
    only ``too_large=True`` when the body passed ``MAX_UPLOAD_BODY`` and was discarded."""

    texts: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    files: Mapping[str, bytes] = field(default_factory=lambda: MappingProxyType({}))
    too_large: bool = False


def multipart_boundary(content_type: str) -> bytes:
    """The boundary of a ``multipart/form-data`` ``Content-Type`` value; raises
    ``MalformedForm("not_multipart")``."""
    media, _, rest = content_type.partition(";")
    if media.strip().lower() != MULTIPART_MEDIA_TYPE:
        raise MalformedForm(NOT_MULTIPART)
    boundaries = []
    for parameter in rest.split(";") if rest else ():
        found = PARAMETER_PATTERN.fullmatch(parameter)
        if found is None:
            raise MalformedForm(NOT_MULTIPART)
        name, value = found.group(1).lower(), found.group(2)
        if name == "boundary":
            boundaries.append(value[1:-1] if value.startswith('"') else value)
    if len(boundaries) != 1 or not BOUNDARY_PATTERN.fullmatch(boundaries[0]):
        raise MalformedForm(NOT_MULTIPART)
    return boundaries[0].encode("ascii")


def _part(part: bytes) -> tuple[str, str | bytes]:
    """One part's name and value: bytes for a file, text otherwise."""
    head, separator, content = part.partition(HEADER_END)
    if not separator:
        raise MalformedForm(BAD_BODY)
    try:
        lines = head.decode("utf-8", errors="strict").split("\r\n")
    except UnicodeDecodeError as exc:
        raise MalformedForm(BAD_BODY) from exc
    disposition: re.Match[str] | None = None
    content_types = 0
    for line in lines:
        name, colon, value = line.partition(":")
        header = name.lower()
        if not colon or name != name.strip():
            raise MalformedForm(BAD_BODY)
        if header == "content-disposition" and disposition is None:
            disposition = DISPOSITION_PATTERN.fullmatch(value.strip())
            if disposition is None:
                raise MalformedForm(BAD_BODY)
        elif header == "content-type" and content_types == 0:
            content_types += 1
        else:
            raise MalformedForm(BAD_BODY)
    if disposition is None:
        raise MalformedForm(BAD_BODY)
    if disposition.group(2) is not None:
        return disposition.group(1), content
    try:
        return disposition.group(1), content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise MalformedForm(BAD_BODY) from exc


def parse_multipart(body: bytes, boundary: bytes) -> UploadForm:
    """The fields of a ``multipart/form-data`` body; raises ``MalformedForm``."""
    delimiter = b"--" + boundary
    opening = delimiter + CRLF
    closing = CRLF + delimiter + b"--"
    if body.endswith(closing + CRLF):
        end = len(body) - len(closing) - len(CRLF)
    elif body.endswith(closing):
        end = len(body) - len(closing)
    else:
        raise MalformedForm(BAD_BODY)
    if not body.startswith(opening) or end < len(opening):
        raise MalformedForm(BAD_BODY)
    parts = body[len(opening) : end].split(CRLF + opening)
    texts: dict[str, str] = {}
    files: dict[str, bytes] = {}
    for part in parts:
        if CRLF + delimiter in part:
            raise MalformedForm(BAD_BODY)
        name, value = _part(part)
        if name in texts or name in files:
            raise MalformedForm(REPEATED_FIELD)
        if isinstance(value, bytes):
            files[name] = value
        else:
            texts[name] = value
    return UploadForm(MappingProxyType(texts), MappingProxyType(files))


async def upload_form(request: Request) -> UploadForm:
    """The fields of the request's ``multipart/form-data`` body; raises ``MalformedForm``."""
    content_types = request.headers.getlist("content-type")
    if len(content_types) != 1:
        raise MalformedForm(NOT_MULTIPART)
    boundary = multipart_boundary(content_types[0])
    chunks: list[bytes] = []
    size = 0
    too_large = False
    async for chunk in request.stream():
        if too_large:
            continue  # drained and discarded
        size += len(chunk)
        if size > MAX_UPLOAD_BODY:
            too_large = True
            chunks.clear()
            continue
        chunks.append(chunk)
    if too_large:
        return UploadForm(too_large=True)
    return parse_multipart(b"".join(chunks), boundary)


def malformed_form(request: Request, exc: Exception) -> Response:
    """The exception handler for ``MalformedForm``: 400 with the plain refusal."""
    log.warning(
        "request_refused",
        extra={
            "check": FORM_CHECK,
            "reason": getattr(exc, "reason", BAD_BODY),
            "method": request.method,
            "path": request.scope.get("path"),
        },
    )
    return PlainTextResponse(
        MALFORMED_FORM, status_code=400, headers={"Cache-Control": "no-store"}
    )
