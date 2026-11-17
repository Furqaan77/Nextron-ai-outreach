"""The CSV Lead import page (FR-59, UX-DR20, AD-13, AD-18): views for ``create_app``'s two
routes, which stay thin.

- ``GET /leads/import`` (``lead_import_page``, ``@personal_data(False)``: it shows Segment names
  only) renders ``lead_import.html``, which extends ``base.html`` and includes
  ``lead_import_form.html``.
- ``POST /leads/import`` (``lead_import``, ``@personal_data(True)``) takes the body through
  ``nextron.web.forms.upload_form``, checks its fields with ``submitted_import``, runs
  ``LeadImports.run`` in its own unit of work and answers 200 with ``lead_import_form.html``
  alone, which HTMX swaps in for the page's ``#lead-import`` block (the form posts with
  ``hx-encoding="multipart/form-data"``, and ``hx-disabled-elt`` disables its button while the
  import runs).

**The form** (``method="post"``, ``enctype="multipart/form-data"``, ``hx-post``,
``{{ csrf_field() }}``) holds the list's origin (required, 3 to 200 characters, with an
example in its hint), the default Segment (``No default``, then each stored Segment by name,
its value the Segment's key) and the CSV file (``accept=".csv,text/csv"``). ``submitted_import``
allows only the texts ``csrf_token``, ``origin`` and ``default_segment`` and the file ``file``,
and needs the last three; anything else is ``MalformedForm("wrong_fields")`` (400). A blank
default is no default.

**The answers:**

- refused (``ImportRefused``): the reason by its field (``origin``, ``default_segment`` or
  ``file``, marked ``aria-invalid``), with the origin and default typed so far kept;
- over the body limit (``UploadForm.too_large``): "The file is over 5 MB." by the file, with the
  origin and default reset, because the body was discarded unread;
- imported: the summary above a fresh form: the imported, merged and rejected counts, each
  rejected row (number, label, reason) and each imported row with notes (number, label, notes);
- stopped (``ImportStopped``): the summary so far and ``stop_notice(at_row)``, with the origin
  and default kept, since the notice says to import the same file again.

A row's label is CSV text, so it renders through ``untrusted_block`` (escaped, inside
``hx-disable``); the origin is echoed only as an escaped ``value``. Reasons and notes are fixed
texts. The file name is never shown.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from nextron.app.discovery.api import ImportSummary
from nextron.app.queries.setup import SeededSegment
from nextron.domain.lead_import import FIELD_FILE, FILE_TOO_LARGE, ImportRefused
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import WRONG_FIELDS, MalformedForm, UploadForm

PAGE_TEMPLATE: Final = "lead_import.html"
FORM_TEMPLATE: Final = "lead_import_form.html"
ORIGIN_FIELD: Final = "origin"
DEFAULT_SEGMENT_FIELD: Final = "default_segment"
FILE_FIELD: Final = "file"
TEXT_FIELDS: Final = frozenset({CSRF_FIELD, ORIGIN_FIELD, DEFAULT_SEGMENT_FIELD})
REQUIRED_TEXTS: Final = frozenset({ORIGIN_FIELD, DEFAULT_SEGMENT_FIELD})
FILE_FIELDS: Final = frozenset({FILE_FIELD})


@dataclass(frozen=True, slots=True)
class SegmentOption:
    """One default Segment choice: its key (the value) and its name (the label)."""

    key: str
    name: str


@dataclass(frozen=True, slots=True)
class SubmittedImport:
    """What the form sent: the origin as typed, the default Segment's key (``None`` for no
    default) and the file's bytes."""

    origin: str
    default_segment: str | None
    data: bytes


def segment_options(segments: Iterable[SeededSegment]) -> tuple[SegmentOption, ...]:
    """Each stored Segment as a choice, by name."""
    options = (SegmentOption(segment.key, segment.definition.name) for segment in segments)
    return tuple(sorted(options, key=lambda option: (option.name.casefold(), option.key)))


def submitted_import(form: UploadForm) -> SubmittedImport:
    """The form's fields; ``MalformedForm`` for any other field or a missing one."""
    texts = set(form.texts)
    if (
        not texts <= TEXT_FIELDS
        or not REQUIRED_TEXTS <= texts
        or set(form.files) != FILE_FIELDS
    ):
        raise MalformedForm(WRONG_FIELDS)
    default = form.texts[DEFAULT_SEGMENT_FIELD]
    return SubmittedImport(
        origin=form.texts[ORIGIN_FIELD],
        default_segment=default or None,
        data=form.files[FILE_FIELD],
    )


def stop_notice(at_row: int) -> str:
    """What the page says when an import stopped at ``at_row``."""
    return (
        f"The import stopped at row {at_row:,}: that batch of rows and everything after it was "
        "not imported, and the rows before it were saved. Import the same file again to "
        "finish: rows already imported are merged, not duplicated."
    )


def form_context(
    *,
    origin: str = "",
    default_segment: str = "",
    errors: dict[str, str] | None = None,
    summary: ImportSummary | None = None,
    stopped: str | None = None,
) -> dict[str, object]:
    """What ``lead_import_form.html`` renders, besides the Segment choices: the origin and
    default to show, each field's error, and the summary and stop notice, if any."""
    return {
        "origin": origin,
        "default_segment": default_segment,
        "errors": dict(errors or {}),
        "summary": summary,
        "stopped": stopped,
    }


def refused_context(submitted: SubmittedImport, refused: ImportRefused) -> dict[str, object]:
    """The form again, with the refusal by its field and what was typed kept."""
    return form_context(
        origin=submitted.origin,
        default_segment=submitted.default_segment or "",
        errors={refused.field: refused.reason},
    )


def too_large_context() -> dict[str, object]:
    """The form again after a body over the limit: the file's error, nothing kept."""
    return form_context(errors={FIELD_FILE: FILE_TOO_LARGE})
