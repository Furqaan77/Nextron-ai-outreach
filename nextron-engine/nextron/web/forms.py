"""Urlencoded form bodies of dashboard POSTs (AD-13).

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
``bad_body``, ``wrong_fields`` or ``repeated_field``), the ``method`` and the ``path``; never a
field name or value. HTMX doesn't swap the 400, so the form stays as it was (the notification
arrives in Story 1.12).

``require_csrf`` runs first and parses an urlencoded body itself, so a repeated
``Content-Type``, a non-ASCII urlencoded body or more than 1,000 fields get its 403
(``Request refused: security check failed``) before ``form_fields`` sees them. The same checks
stay here as defence in depth; what reaches this module's 400 is a body that isn't urlencoded,
a bad escape or field (``%FF``, ``&&``, a field without ``=``), and fields other than the
form's own.
"""

import logging
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
