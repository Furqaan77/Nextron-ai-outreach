"""``nextron.domain.lead_import`` (FR-59, FR-9, FR-15, FR-49): Story 2.3's planner rows of the
I/O matrix (happy path, refused files, rejected rows, a failing email), the header aliases, the
limits, the row numbering, the Contact cell notes and ``check_origin``; and the import's
Segment lookup (``nextron.app.discovery.api._segment_lookup``)."""

import dataclasses
import inspect
import tracemalloc

import pytest

from nextron import main
from nextron.app.discovery import api as discovery_api
from nextron.app.queries.setup import SeededSegment
from nextron.domain import lead_import, seed_yaml
from nextron.domain.contacts import ContactCandidate
from nextron.domain.errors import DomainError
from nextron.domain.lead_import import (
    FILE_EMPTY,
    FILE_HAS_NUL,
    FILE_NO_HEADER,
    FILE_NOT_UTF8,
    FILE_TOO_LARGE,
    FILE_TOO_MANY_ROWS,
    HEADERS,
    LABEL_LENGTH,
    MAX_FILE_BYTES,
    MAX_ROWS,
    NOTE_BAD_EMAIL,
    NOTE_BAD_LINKEDIN,
    NOTE_BAD_NAME,
    NOTE_BAD_ROLE,
    NOTE_NO_CONTACT,
    ORIGIN_CHARACTERS,
    ORIGIN_LENGTH,
    REASON_BAD_COUNTRY,
    REASON_BAD_NAME,
    REASON_BAD_WEBSITE,
    REASON_DENIED,
    REASON_EXTRA_CELLS,
    REASON_NO_SEGMENT,
    REASON_SHARED_PLATFORM,
    REASON_UNKNOWN_SEGMENT,
    ImportField,
    ImportPlan,
    ImportRefused,
    ImportRow,
    RejectedRow,
    check_file_size,
    check_origin,
    plan_import,
)
from nextron.domain.leads import LeadIdentity
from nextron.domain.segments import SegmentDef, validate_segments
from tests.fakes.email_syntax import FakeEmailSyntax

LAW = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
OPS = "01JD3QZ8X5V9K2M7N4P6R8T0WZ"
SEGMENTS = {
    "law_private_ai": LAW,
    "law-privateai": LAW,
    "ops_automation": OPS,
    "ops-automation": OPS,
}
HEADER = "Organization name,Website,Country"
BOM = b"\xef\xbb\xbf"


def csv_bytes(*lines: str, bom: bool = False) -> bytes:
    data = "\r\n".join(lines).encode("utf-8") + b"\r\n"
    return BOM + data if bom else data


def plan(
    data: bytes,
    *,
    default: str | None = LAW,
    email_ok: FakeEmailSyntax | None = None,
) -> ImportPlan:
    checker = email_ok if email_ok is not None else FakeEmailSyntax()
    return plan_import(
        data, segments=SEGMENTS, default_segment_id=default, email_ok=checker.is_valid
    )


def refusal(data: bytes, **kwargs: object) -> ImportRefused:
    with pytest.raises(ImportRefused) as caught:
        plan(data, **kwargs)  # type: ignore[arg-type]
    assert caught.value.field == "file"
    return caught.value


def reasons(found: ImportPlan) -> list[tuple[int, str, str]]:
    return [(row.number, row.label, row.reason) for row in found.rejected]


# The I/O matrix -------------------------------------------------------------------------------


def test_the_happy_path() -> None:
    data = csv_bytes(
        "Organisation Name,WEBSITE,Country,Contact Name,Email",
        "Smith Law,https://www.smith-law.co.uk/team,UK,Jane Doe,Jane@Smith-Law.co.uk",
        "Smith Law LLP,smith-law.co.uk,GB,,",
        bom=True,
    )

    found = plan(data)

    assert found == ImportPlan(
        rows=(
            ImportRow(
                2,
                "Smith Law",
                LeadIdentity("Smith Law", LAW, web_domain="smith-law.co.uk", country_code="GB"),
                ContactCandidate(name="Jane Doe", email="jane@smith-law.co.uk"),
                (),
            ),
            ImportRow(
                3,
                "Smith Law LLP",
                LeadIdentity(
                    "Smith Law LLP", LAW, web_domain="smith-law.co.uk", country_code="GB"
                ),
                None,
                (),
            ),
        ),
        rejected=(),
    )


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"", FILE_EMPTY),
        (b"x" * (MAX_FILE_BYTES + 1), FILE_TOO_LARGE),
        (csv_bytes(HEADER, "Caf\xe9 Law,cafe.fr,FR").replace(b"\xc3\xa9", b"\xe9"), FILE_NOT_UTF8),
        (
            csv_bytes("Organization name,Country", "Smith Law,GB"),
            "The header has no website column.",
        ),
        (csv_bytes(HEADER, "a\x00b,x.com,GB"), FILE_HAS_NUL),
        (BOM, FILE_NO_HEADER),
        (
            csv_bytes("Company,Organisation,Website,Country", "a,b,x.com,GB"),
            "The header names the organization name column twice.",
        ),
        (csv_bytes(HEADER, '"Smith" Law,x.com,GB'), "The file isn't valid CSV (row 2: "),
        (csv_bytes(HEADER, 'Smith,"x.com,GB'), "The file isn't valid CSV (row 2: "),
    ],
)
def test_a_file_that_cant_be_imported_is_refused(data: bytes, reason: str) -> None:
    error = refusal(data)

    assert error.reason.startswith(reason)
    assert error.message == f"file: {error.reason}"


def test_more_than_5000_counted_rows_are_refused() -> None:
    rows = [f"Firm {n},firm{n}.com,GB" for n in range(MAX_ROWS + 1)]

    assert refusal(csv_bytes(HEADER, *rows)).reason == FILE_TOO_MANY_ROWS


def test_exactly_5000_rows_and_blank_rows_pass() -> None:
    rows = [f"Firm {n},firm{n}.com,GB" for n in range(MAX_ROWS)]

    found = plan(csv_bytes(HEADER, *rows, ",,", "", " , , "))

    assert len(found.rows) == MAX_ROWS
    assert found.rows[-1].number == MAX_ROWS + 1


def test_a_file_of_exactly_5_mib_is_read() -> None:
    line = b"Firm,firm.com,GB\r\n"
    head = HEADER.encode() + b"\r\n"
    filler = MAX_FILE_BYTES - len(head) - len(line)
    data = head + line + b",\n" * (filler // 2) + b" " * (filler % 2)  # blank records

    assert len(data) == MAX_FILE_BYTES
    assert len(plan(data).rows) == 1


def test_the_rejected_rows_of_the_matrix() -> None:
    data = csv_bytes(
        "Organization name,Website,Country,Segment",
        ",blank-name.com,GB,",
        "Not a URL,not a url,GB,",
        "LinkedIn,https://www.linkedin.com/company/x,GB,",
        "Maps,https://www.google.com/maps/place/x,GB,",
        "Indeed,uk.indeed.com/jobs,GB,",
        "Sites,sites.google.com/view/x,GB,",
        "Zed,zed.com,ZZ,",
        "Nope,nope.com,GB,Nope",
        "Extra,extra.com,GB,,surplus",
    )

    found = plan(data)

    assert found.rows == ()
    assert reasons(found) == [
        (2, "", "missing organization name"),
        (3, "Not a URL", REASON_BAD_WEBSITE),
        (4, "LinkedIn", REASON_DENIED),
        (5, "Maps", REASON_DENIED),
        (6, "Indeed", REASON_DENIED),
        (7, "Sites", REASON_SHARED_PLATFORM),
        (8, "Zed", REASON_BAD_COUNTRY),
        (9, "Nope", REASON_UNKNOWN_SEGMENT),
        (10, "Extra", REASON_EXTRA_CELLS),
    ]


def test_a_blank_segment_with_no_default_is_rejected() -> None:
    found = plan(csv_bytes(HEADER, "Firm,firm.com,GB"), default=None)

    assert reasons(found) == [(2, "Firm", REASON_NO_SEGMENT)]


def test_an_unknown_segment_is_rejected_even_with_a_default() -> None:
    found = plan(csv_bytes(HEADER + ",Segment", "Firm,firm.com,GB,Lawyers"))

    assert reasons(found) == [(2, "Firm", REASON_UNKNOWN_SEGMENT)]


@pytest.mark.parametrize(
    "cell", ["law_private_ai", "LAW_PRIVATE_AI", "Law-PrivateAI", "ops_automation"]
)
def test_a_segment_matches_by_key_or_name_ignoring_case(cell: str) -> None:
    [row] = plan(csv_bytes(HEADER + ",Segment", f"Firm,firm.com,GB,{cell}"), default=None).rows

    assert row.identity.segment_id == SEGMENTS[cell.casefold()]


def test_the_missing_fields_in_order() -> None:
    found = plan(csv_bytes(HEADER, ",,GB", "Firm,,", "Firm", "Firm,firm.com"))

    assert reasons(found) == [
        (2, "", "missing organization name"),
        (3, "Firm", "missing website"),
        (4, "Firm", "missing website"),
        (5, "Firm", "missing country"),
    ]


def test_a_bad_organization_name_is_rejected() -> None:
    found = plan(csv_bytes(HEADER, f"{'x' * 201},x.com,GB", "a\x07b,y.com,GB", "a​b,z.com,GB"))

    assert [(number, reason) for number, _, reason in reasons(found)] == [
        (2, REASON_BAD_NAME),
        (3, REASON_BAD_NAME),
        (4, REASON_BAD_NAME),
    ]
    assert found.rejected[0].label == "x" * LABEL_LENGTH
    assert found.rejected[1].label == "ab"  # control and format characters leave the label
    assert found.rejected[2].label == "ab"


def test_the_checks_run_in_order() -> None:
    # a denied website with an unknown country and Segment: the website check comes first
    found = plan(csv_bytes(HEADER + ",Segment", "X,linkedin.com/in/x,ZZ,Nope"))

    assert reasons(found) == [(2, "X", REASON_DENIED)]


# Contacts -------------------------------------------------------------------------------------


def test_an_email_that_fails_the_syntax_check_is_dropped_with_a_note() -> None:
    data = csv_bytes(HEADER + ",Contact name,Email", 'Firm,firm.com,GB,Ann Lee,"a,b@firm.com"')

    [row] = plan(data).rows

    assert row.contact == ContactCandidate(name="Ann Lee")
    assert row.notes == (NOTE_BAD_EMAIL,)


def test_the_email_is_normalised_before_the_syntax_check() -> None:
    checker = FakeEmailSyntax(refused={"blocked@firm.com"})
    data = csv_bytes(
        HEADER + ",Email", "Firm,firm.com,GB, Ann@Firm.COM ", "Other,other.com,GB,BLOCKED@firm.com"
    )

    first, second = plan(data, email_ok=checker).rows

    assert checker.checked == ["ann@firm.com", "blocked@firm.com"]
    assert first.contact == ContactCandidate(email="ann@firm.com")
    assert second.contact is None
    assert second.notes == (NOTE_BAD_EMAIL, NOTE_NO_CONTACT)


def test_each_bad_contact_cell_is_dropped_with_its_note() -> None:
    data = csv_bytes(
        HEADER + ",Contact,Role,Email,LinkedIn",
        f"A,a.com,GB,Ann,{'r' * 201},ann@a.com,linkedin.com/company/a",
        f"B,b.com,GB,{'n' * 201},CEO,ben@b.com,linkedin.com/in/Ben",
        "C,c.com,GB,,Partner,not-an-email,",
        "D,d.com,GB,---,,,",
    )

    a, b, c, d = plan(data).rows

    assert a.contact == ContactCandidate(name="Ann", email="ann@a.com")
    assert a.notes == (NOTE_BAD_LINKEDIN, NOTE_BAD_ROLE)
    assert b.contact == ContactCandidate(
        role="CEO", email="ben@b.com", linkedin_url="https://www.linkedin.com/in/ben"
    )
    assert b.notes == (NOTE_BAD_NAME,)
    assert c.contact is None
    assert c.notes == (NOTE_BAD_EMAIL, NOTE_NO_CONTACT)
    assert d.contact is None
    assert d.notes == (NOTE_BAD_NAME, NOTE_NO_CONTACT)


def test_a_row_without_contact_cells_has_no_contact_and_no_note() -> None:
    [row] = plan(csv_bytes(HEADER + ",Contact,Email", "Firm,firm.com,GB,,")).rows

    assert (row.contact, row.notes) == (None, ())


def test_a_full_contact() -> None:
    data = csv_bytes(
        "Company,URL,Country Code,Contact Name,Job Title,Email Address,LinkedIn URL",
        "Firm,firm.com,us,Ann  Lee,Managing   Partner,ann@firm.com,https://linkedin.com/in/ann-lee",
    )

    [row] = plan(data).rows

    assert row.identity.country_code == "US"
    assert row.contact == ContactCandidate(
        name="Ann Lee",
        role="Managing Partner",
        email="ann@firm.com",
        linkedin_url="https://www.linkedin.com/in/ann-lee",
    )


# Headers, cells and numbering -----------------------------------------------------------------


ALIASES = [
    ("organization name", ImportField.ORGANIZATION),
    ("Organisation_Name", ImportField.ORGANIZATION),
    ("ORGANIZATION", ImportField.ORGANIZATION),
    ("organisation", ImportField.ORGANIZATION),
    ("Company", ImportField.ORGANIZATION),
    ("company-name", ImportField.ORGANIZATION),
    ("Website", ImportField.WEBSITE),
    ("Website URL", ImportField.WEBSITE),
    ("web site", ImportField.WEBSITE),
    ("URL", ImportField.WEBSITE),
    ("Country", ImportField.COUNTRY),
    ("country_code", ImportField.COUNTRY),
    ("Segment", ImportField.SEGMENT),
    ("Contact Name", ImportField.CONTACT_NAME),
    ("contact", ImportField.CONTACT_NAME),
    ("Role", ImportField.ROLE),
    ("Contact Role", ImportField.ROLE),
    ("Title", ImportField.ROLE),
    ("Job  Title", ImportField.ROLE),
    ("Email", ImportField.EMAIL),
    ("EMAIL", ImportField.EMAIL),
    ("Email Address", ImportField.EMAIL),
    ("Contact-Email", ImportField.EMAIL),
    ("LinkedIn URL", ImportField.LINKEDIN),
    ("linkedin", ImportField.LINKEDIN),
    ("Contact LinkedIn URL", ImportField.LINKEDIN),
]


@pytest.mark.parametrize(("header", "field"), ALIASES)
def test_each_header_alias(header: str, field: ImportField) -> None:
    assert HEADERS[lead_import._header_key(header)] is field


def test_the_alias_table_is_exactly_the_design_notes() -> None:
    assert sorted(HEADERS) == sorted(
        [
            "organization name",
            "organisation name",
            "organization",
            "organisation",
            "company",
            "company name",
            "website",
            "website url",
            "web site",
            "url",
            "country",
            "country code",
            "segment",
            "contact name",
            "contact",
            "role",
            "contact role",
            "title",
            "job title",
            "email",
            "email address",
            "contact email",
            "linkedin url",
            "linkedin",
            "contact linkedin url",
        ]
    )


def test_unknown_headers_are_ignored_and_their_cells_never_kept() -> None:
    data = csv_bytes("Notes,Website,Phone,Company,Country", "secret note,firm.com,555,Firm,GB")

    found = plan(data)

    assert len(found.rows) == 1
    assert "secret" not in repr(found)
    assert "555" not in repr(found)


def test_row_numbers_count_the_header_as_row_1_blank_rows_included() -> None:
    data = csv_bytes(
        HEADER,
        "A,a.com,GB",
        "",
        ",,",
        '"B\r\nLLP",b.com,GB',  # one record over two lines, as a spreadsheet keeps it
        "C,c.com,ZZ",
        "D,d.com,GB",
    )

    found = plan(data)

    assert [(row.number, row.label) for row in found.rows] == [
        (2, "A"),
        (5, "B LLP"),
        (7, "D"),
    ]
    assert reasons(found) == [(6, "C", REASON_BAD_COUNTRY)]


def test_cells_are_whitespace_collapsed_and_missing_cells_are_blank() -> None:
    data = csv_bytes(HEADER + ",Contact", "  Smith \t Law  , smith.com ,  gb ")

    [row] = plan(data).rows

    assert row.identity == LeadIdentity("Smith Law", LAW, web_domain="smith.com", country_code="GB")
    assert row.contact is None


def test_labels_are_cut_to_100_characters() -> None:
    long_name = "L" * 150

    [row] = plan(csv_bytes(HEADER, f"{long_name},firm.com,GB")).rows

    assert row.identity.name == long_name
    assert row.label == "L" * LABEL_LENGTH


def test_a_lone_cr_or_lf_line_ending_works_too() -> None:
    data = (HEADER + "\nA,a.com,GB\rB,b.com,GB\n").encode()

    assert [row.label for row in plan(data).rows] == ["A", "B"]


def test_the_identity_is_built_from_normaliser_output() -> None:
    [row] = plan(csv_bytes(HEADER, "Firm,HTTPS://WWW.FIRM.CO.UK./x?y=1,uk")).rows

    assert row.identity.web_domain == "firm.co.uk"
    assert row.identity.country_code == "GB"


# The origin and the size ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("abc", "abc"),
        ("  Law  Society\tdirectory\n2026 ", "Law Society directory 2026"),
        ("x" * 200, "x" * 200),
    ],
)
def test_check_origin_cleans_the_text(text: str, expected: str) -> None:
    assert check_origin(text) == expected


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("ab", ORIGIN_LENGTH),
        ("  a  ", ORIGIN_LENGTH),
        ("", ORIGIN_LENGTH),
        ("x" * 201, ORIGIN_LENGTH),
        ("abc\x07", ORIGIN_CHARACTERS),
        ("abc‮", ORIGIN_CHARACTERS),
    ],
)
def test_check_origin_refuses(text: str, reason: str) -> None:
    with pytest.raises(ImportRefused) as caught:
        check_origin(text)

    assert (caught.value.field, caught.value.reason) == ("origin", reason)
    assert isinstance(caught.value, DomainError)
    assert caught.value.code == "lead_import_refused"


def test_check_file_size() -> None:
    check_file_size(b"x")
    check_file_size(b"x" * MAX_FILE_BYTES)
    with pytest.raises(ImportRefused, match="empty"):
        check_file_size(b"")
    with pytest.raises(ImportRefused, match="over 5 MB"):
        check_file_size(b"x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(TypeError):
        check_file_size("x")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        check_origin(b"abc")  # type: ignore[arg-type]


def test_no_reason_or_note_echoes_a_cell() -> None:
    planted = "plantedzq"
    data = csv_bytes(
        HEADER + ",Segment,Email,LinkedIn",
        f"{planted},{planted} x,GB,,,",
        f"Firm,firm.com,{planted},,,",
        f"Firm,firm.com,GB,{planted},,",
        f"Firm,firm.com,GB,,{planted},{planted}",
    )

    found = plan(data)

    assert all(planted not in row.reason for row in found.rejected)
    assert all(planted not in note for row in found.rows for note in row.notes)


def test_a_rejected_row_is_a_rejected_row() -> None:
    [row] = plan(csv_bytes(HEADER, "Firm,firm.com,ZZ")).rejected

    assert row == RejectedRow(2, "Firm", REASON_BAD_COUNTRY)


# Review fixes ---------------------------------------------------------------------------------


def test_a_contact_name_without_letters_or_digits_is_noted_as_such() -> None:
    data = csv_bytes(HEADER + ",Contact,Email", "Firm,firm.com,GB,---,ann@firm.com")

    [row] = plan(data).rows

    assert row.contact == ContactCandidate(email="ann@firm.com")
    assert row.notes == (NOTE_BAD_NAME,)
    assert "has no letters or digits" in NOTE_BAD_NAME


def test_the_records_are_streamed() -> None:
    assert inspect.isgenerator(lead_import._records("a\n"))


def test_the_row_limit_refuses_as_soon_as_the_5001st_row_is_read() -> None:
    rows = [f"Firm {n},firm{n}.com,GB" for n in range(MAX_ROWS + 1)]
    # a malformed record after the 5,001st row is never reached
    data = csv_bytes(HEADER, *rows, 'X,"x.com,GB')

    assert refusal(data).reason == FILE_TOO_MANY_ROWS


def test_a_csv_error_names_its_record_position() -> None:
    data = csv_bytes("", HEADER, "", "A,a.com,GB", 'B,"b.com,GB')

    assert refusal(data).reason.startswith("The file isn't valid CSV (row 5: ")


def test_blank_records_are_never_held_in_memory() -> None:
    data = HEADER.encode() + b"\r\n" + b"\n" * 300_000 + b"A,a.com,GB\r\n"

    tracemalloc.start()
    try:
        found = plan(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert [row.number for row in found.rows] == [300_002]
    assert peak < 8 * 1024 * 1024  # holding 300,000 empty records took far more


def test_blank_lines_before_the_header_are_skipped() -> None:
    data = csv_bytes("", ",,", "  ", HEADER, "A,a.com,GB", "", "B,b.com,ZZ")

    found = plan(data)

    assert [(row.number, row.label) for row in found.rows] == [(5, "A")]
    assert reasons(found) == [(7, "B", REASON_BAD_COUNTRY)]


def test_a_file_of_blank_lines_has_no_header() -> None:
    assert refusal(csv_bytes("", ",,", " ")).reason == FILE_NO_HEADER


@pytest.mark.parametrize("line", ["A,a.com,GB,", "A,a.com,GB, , \t", "A,a.com,GB,,,"])
def test_blank_cells_past_the_header_are_ignored(line: str) -> None:
    [row] = plan(csv_bytes(HEADER, line)).rows

    assert row.identity.web_domain == "a.com"


@pytest.mark.parametrize("line", ["A,a.com,GB,x", "A,a.com,GB,,,x"])
def test_a_non_blank_cell_past_the_header_rejects_the_row(line: str) -> None:
    assert reasons(plan(csv_bytes(HEADER, line))) == [(2, "A", REASON_EXTRA_CELLS)]


def _shipped() -> dict[str, SegmentDef]:
    text = (main.SEED_DIR / main.SEGMENTS_FILE_NAME).read_text(encoding="utf-8")
    return {found.key: found for found in validate_segments(seed_yaml.parse(text))}


def test_the_segment_lookup_folds_keys_and_names_and_a_key_beats_a_name() -> None:
    shipped = _shipped()
    law = dataclasses.replace(shipped["law_private_ai"], name="Law   Private  AI")
    # the second Segment's name folds to the first one's key
    ops = dataclasses.replace(shipped["ops_automation"], name="LAW_PRIVATE_AI")
    stored = (
        SeededSegment(id=LAW, key="law_private_ai", version=1, definition=law),
        SeededSegment(id=OPS, key="ops_automation", version=1, definition=ops),
    )

    lookup = discovery_api._segment_lookup(stored)

    assert lookup == {
        "law_private_ai": LAW,  # the key wins over the other Segment's name
        "ops_automation": OPS,
        "law private ai": LAW,  # inner whitespace runs collapse, as in a CSV cell
    }
    [row] = plan_import(
        csv_bytes(HEADER + ",Segment", "A,a.com,GB,  LAW   private AI "),
        segments=lookup,
        default_segment_id=None,
        email_ok=FakeEmailSyntax().is_valid,
    ).rows
    assert row.identity.segment_id == LAW
